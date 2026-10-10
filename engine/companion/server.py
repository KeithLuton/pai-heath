"""The one listener that faces the home network, for the paired phone only.

The UI server stays loopback-only. This listener:
  * is off until the user turns on the phone companion;
  * binds to this computer's private LAN address (never 0.0.0.0, never a public address);
  * answers only /v1/ping (no data), /v1/hello and /v1/sync;
  * accepts only bodies sealed with a paired phone's key (companion/protocol.py);
  * rate-limits each address and caps body size;
  * says nothing useful while no user is unlocked (423).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import protocol as P
from .service import is_private_lan

MAX_BODY = 8 * 1024 * 1024
RATE = 120            # requests per minute per address


class _Server(ThreadingHTTPServer):
    """No port sharing: on Windows SO_REUSEADDR would let another program bind the same port,
    so use SO_EXCLUSIVEADDRUSE there (and a busy port fails, so the next one is tried)."""
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class LanSyncServer:
    def __init__(self, app, host: str, port: int):
        if not is_private_lan(host):
            raise ValueError("Refusing to listen on a non-private address")
        self.app, self.host, self.port = app, host, port
        self._server: Optional[ThreadingHTTPServer] = None
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _allowed(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= RATE:
                return False
            q.append(now)
            return True

    def start(self) -> int:
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                return

            def _send(self, status: int, body: bytes, ctype="application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _plain(self, status: int, msg: str) -> None:
                self._send(status, json.dumps({"error": msg}).encode())

            def do_GET(self):  # noqa: N802
                if not outer._allowed(self.client_address[0]):
                    return self._plain(429, "slow down")
                if self.path == "/v1/ping":
                    return self._send(200, json.dumps({"app": "PAi Health", "protocol": P.VERSION}).encode())
                self._plain(404, "not found")

            def do_POST(self):  # noqa: N802
                if not outer._allowed(self.client_address[0]):
                    return self._plain(429, "slow down")
                if self.path not in ("/v1/hello", "/v1/sync"):
                    return self._plain(404, "not found")
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n <= 0 or n > MAX_BODY:
                    return self._plain(413, "bad size")
                body = self.rfile.read(n)
                if not outer.app.vault:
                    return self._plain(423, "PAi Health is locked on the computer")
                try:
                    reply = outer.app.companion.handle(self.path, body)
                except PermissionError as exc:
                    return self._plain(403, str(exc))
                except P.ProtocolError as exc:
                    return self._plain(400, str(exc))
                except Exception:
                    return self._plain(500, "sync failed on the computer")
                self._send(200, reply)

        self._server = _Server((self.host, self.port), H)
        self.port = self._server.server_port
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
