"""Loopback-only server for the PAi Health UI and its engine contract.

Everything lives under /<token>/. Anything outside that prefix is a 404,
including static assets, so nothing about this process is reachable without
the per-run token.

* Binds 127.0.0.1 only (never 0.0.0.0).
* Host header must be exactly 127.0.0.1:<port> (blocks DNS rebinding).
* No Access-Control-Allow-Origin: other origins can't read responses.
* Referrer-Policy no-referrer: the token never leaks in a Referer header.
* POSTs additionally require X-PAi-Request: 1 and a same-origin Origin
  header if one is sent.
* Extra API routes (app setup, users, ingest) are plain functions registered
  by prefix; they inherit every check above. Each POST route declares its own
  body limit (uploads are larger than settings).
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs

LOOPBACK = "127.0.0.1"
CONTRACT_VERSION = "0.4.4"
MAX_BODY = 64 * 1024

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("text/javascript", ".js")

SETTING_VALUES = {
    "data_path": {"REVIEW", "LIVE"},
    "device_dose_confirmation": {True, False},
    "ai_mode": {"None", "PAi", "PSi"},
}

Provider = Callable[[], Tuple[dict, bool]]          # (data, synthetic)
SettingSink = Callable[[str, object], None]          # raises ValueError to refuse
# A route gets (path below its prefix, query dict, request headers, body) and returns
# (status, payload): payload is a dict (sent as JSON) or (bytes, content_type).
Route = Callable[[str, dict, dict, bytes], Tuple[int, object]]


class ApiError(Exception):
    """Raise from a route to answer with a status and a plain message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _match(routes: dict, rel: str):
    """Longest registered prefix of rel -> (route, rest of path), or None."""
    for prefix in sorted(routes, key=len, reverse=True):
        if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
            return routes[prefix], rel[len(prefix):].lstrip("/")
    return None


class HealthServer:
    def __init__(self, dist_dir: Path, scenario: Provider, tracker: Provider,
                 on_setting: SettingSink, get_routes: Optional[Dict[str, Route]] = None,
                 post_routes: Optional[Dict[str, Tuple[Route, int]]] = None):
        self.dist_dir = Path(dist_dir).resolve()
        if not (self.dist_dir / "index.html").is_file():
            raise FileNotFoundError(f"UI build not found at {self.dist_dir}")
        self.token = secrets.token_urlsafe(24)
        self.scenario, self.tracker, self.on_setting = scenario, tracker, on_setting
        self.get_routes = dict(get_routes or {})
        self.post_routes = dict(post_routes or {})
        self._server: Optional[ThreadingHTTPServer] = None

    @property
    def url(self) -> str:
        assert self._server
        return f"http://{LOOPBACK}:{self._server.server_port}/{self.token}/"

    def start(self) -> str:
        if self._server:
            return self.url
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                return

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                 "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                 "base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj: dict) -> None:
                self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def _dispatch(self, fn: Route, rest: str, body: bytes) -> None:
                query = {k: v[-1] for k, v in parse_qs(self.path.split("?", 1)[1]).items()} \
                    if "?" in self.path else {}
                try:
                    status, payload = fn(rest, query, dict(self.headers), body)
                except ApiError as exc:
                    self._json(exc.status, {"error": str(exc)})
                    return
                except Exception as exc:  # engine failure is reported, never hidden
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                if isinstance(payload, tuple):
                    self._send(status, payload[0], payload[1])
                else:
                    self._json(status, payload)

            def _route(self) -> Optional[str]:
                """Return the path below /<token>/, or None after sending an error."""
                if self.headers.get("Host", "") != f"{LOOPBACK}:{outer._server.server_port}":
                    self._send(421, b"Misdirected request", "text/plain; charset=utf-8")
                    return None
                path = self.path.split("?", 1)[0]
                prefix = f"/{outer.token}/"
                if path == prefix[:-1]:
                    self.send_response(308)
                    self.send_header("Location", prefix)
                    self.send_header("Content-Length", "0")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.end_headers()
                    return None
                if not path.startswith(prefix):
                    self._send(404, b"Not found", "text/plain; charset=utf-8")
                    return None
                return path[len(prefix):]

            def do_GET(self) -> None:  # noqa: N802
                rel = self._route()
                if rel is None:
                    return
                if rel in ("api/scenario.json", "api/tracker.json"):
                    provider = outer.scenario if rel == "api/scenario.json" else outer.tracker
                    try:
                        data, synthetic = provider()
                    except Exception as exc:  # engine failure is reported, never hidden
                        self._json(500, {"error": str(exc)})
                        return
                    self._json(200, {"contract_version": CONTRACT_VERSION,
                                     "synthetic": bool(synthetic), "data": data})
                    return
                if rel.startswith("api/"):
                    route = _match(outer.get_routes, rel)
                    if route is None:
                        self._send(404, b"Not found", "text/plain; charset=utf-8")
                        return
                    self._dispatch(route[0], route[1], b"")
                    return
                target = (outer.dist_dir / (rel or "index.html")).resolve()
                try:
                    target.relative_to(outer.dist_dir)
                except ValueError:
                    self._send(404, b"Not found", "text/plain; charset=utf-8")
                    return
                if not target.is_file():
                    self._send(404, b"Not found", "text/plain; charset=utf-8")
                    return
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
                    ctype += "; charset=utf-8"
                self._send(200, target.read_bytes(), ctype)

            def do_POST(self) -> None:  # noqa: N802
                rel = self._route()
                if rel is None:
                    return
                origin = self.headers.get("Origin")
                if self.headers.get("X-PAi-Request") != "1" or (
                        origin is not None and origin != f"http://{LOOPBACK}:{outer._server.server_port}"):
                    self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                    return
                if rel != "api/settings":
                    route = _match(outer.post_routes, rel)
                    if route is None:
                        self._send(404, b"Not found", "text/plain; charset=utf-8")
                        return
                    (fn, limit), rest = route
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        length = -1
                    if length < 0 or length > limit:
                        self._send(413, b"Bad request size", "text/plain; charset=utf-8")
                        return
                    self._dispatch(fn, rest, self.rfile.read(length) if length else b"")
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BODY:
                    self._send(413, b"Bad request size", "text/plain; charset=utf-8")
                    return
                try:
                    body = json.loads(self.rfile.read(length))
                    key, value = body["key"], body["value"]
                    if key not in SETTING_VALUES or value not in SETTING_VALUES[key]:
                        raise ValueError(f"Unsupported setting {key!r}={value!r}")
                    outer.on_setting(key, value)
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {"ok": True})

        self._server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
