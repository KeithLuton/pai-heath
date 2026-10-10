"""PAi Health desktop app.

    python engine\\app.py [--kb C:\\PAiHealth\\psi_health_kb.db] [--no-browser]

Opens the UI on 127.0.0.1. First run: the computer is checked and private AI
options are shown (or an external provider: NVIDIA Nemotron, OpenAI GPT,
Anthropic Claude), then a user is created. Each user's data lives in their own
encrypted vault under %LOCALAPPDATA%\\PAiHealth\\users.

The knowledge base is picked up from --kb, or from
%LOCALAPPDATA%\\PAiHealth\\knowledge\\psi_health_kb*.db.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime                                          # noqa: E402
from app_state import App, AppError                     # noqa: E402
from health_server import ApiError, HealthServer         # noqa: E402

ROOT = runtime.install_root()
SMALL, UPLOAD = 16 * 1024, 512 * 1024 * 1024


def _body(raw: bytes) -> dict:
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ApiError(400, "Invalid JSON") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "Invalid JSON")
    return data


def _header(headers: dict, name: str) -> str:
    return next((v for k, v in headers.items() if k.lower() == name.lower()), "")


def build_routes(app: App):
    def wrap(fn):
        def route(rest, query, headers, body):
            try:
                return fn(rest, query, headers, body)
            except AppError as exc:
                raise ApiError(exc.status, str(exc)) from exc
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        return route

    def page(rest, q, h, b):
        doc_id, _, n = rest.rpartition("/")
        if not n.isdigit():
            raise ApiError(404, "No page")
        return 200, (app.page_png(doc_id, int(n)), "image/png")

    def upload(rest, q, h, b):
        name = urllib.parse.unquote(_header(h, "X-File-Name")) or "upload"
        opts = {"timezone": _header(h, "X-Time-Zone"), "units": _header(h, "X-Units")}
        return 200, app.upload(Path(name).name, b, opts)

    def restore(rest, q, h, b):
        u = lambda k: urllib.parse.unquote(_header(h, k))   # noqa: E731
        return 200, app.restore(b, u("X-User-Name"), u("X-Backup-Passphrase"), u("X-Vault-Passphrase"))

    def food_search(rest, q, h, b):
        text = str(q.get("q", ""))[:100]
        return 200, {"foods": app.search_foods(text)}

    def food_portions_route(rest, q, h, b):
        part = rest.strip("/")
        if not part.isdigit():
            raise ApiError(400, "Food ID must be a number")
        return 200, app.food_portions(int(part))

    def question(rest, q, h, b):
        text = str(_body(b).get("text", "")).strip()
        if not text:
            raise ApiError(400, "Enter a question")
        app.need_vault().put("QUESTION", {"text": text, "opener": "USER"})
        app._scenario = None
        return 200, {"ok": True}

    get = {
        "api/app": lambda r, q, h, b: (200, app.app_state()),
        "api/system": lambda r, q, h, b: (200, app.system(rescan=q.get("rescan") == "1")),
        "api/jobs": lambda r, q, h, b: (200, app.jobs.get(r)),
        "api/ingest/list": lambda r, q, h, b: (200, {"documents": app.documents()}),
        "api/ingest/doc": lambda r, q, h, b: (200, app.document(r)),
        "api/ingest/page": page,
        "api/ai/consent": lambda r, q, h, b: (200, app.consent_text(r)),
        "api/bluetooth/status": lambda r, q, h, b: (200, app.bluetooth_status()),
        "api/devices/list": lambda r, q, h, b: (200, {"batches": app.device_batches()}),
        "api/investigations": lambda r, q, h, b: (200, {"investigations": app.investigations()}),
        "api/devices/imports": lambda r, q, h, b: (200, {"imports": app.device_imports()}),
        "api/companion": lambda r, q, h, b: (200, app.companion_state()),
        "api/about": lambda r, q, h, b: (200, app.about()),
        "api/billing/profiles": lambda r, q, h, b: (200, {"profiles": app.billing_profiles()}),
        "api/foods/search": food_search,
        "api/foods/portions": food_portions_route,
    }
    post = {
        "api/users/create": (lambda r, q, h, b: (200, app.create_user(_body(b).get("name", ""),
                                                                      _body(b).get("passphrase", ""),
                                                                      _body(b).get("notice"))), SMALL),
        "api/backup": (lambda r, q, h, b: (200, app.backup(str(_body(b).get("passphrase", "")))), SMALL),
        "api/users/unlock": (lambda r, q, h, b: (200, app.unlock(_body(b).get("id", ""),
                                                                 _body(b).get("passphrase", ""))), SMALL),
        "api/users/passphrase": (lambda r, q, h, b: (200, app.change_passphrase(
            _body(b).get("old", ""), _body(b).get("new", ""))), SMALL),
        "api/users/lock": (lambda r, q, h, b: (app.lock_user(), (200, {"ok": True}))[1], SMALL),
        "api/ai/install": (lambda r, q, h, b: (200, {"job": app.install_local(_body(b).get("model"),
                                                                              _body(b).get("checker"))}), SMALL),
        "api/ai/external/models": (lambda r, q, h, b: (200, app.external_models(
            _body(b).get("provider", ""), _body(b).get("api_key", ""))), SMALL),
        "api/ai/external/save": (lambda r, q, h, b: (200, app.save_external(**{
            k: _body(b).get(k) for k in ("provider", "model", "api_key", "allow_pages", "allow_values", "consent",
                                          "allow_investigate")})),
            SMALL),
        "api/ingest/upload": (upload, UPLOAD),
        "api/backup/restore": (restore, UPLOAD),
        "api/ingest/resolve": (lambda r, q, h, b: (200, app.resolve(_body(b).get("doc_id", ""),
                                                                    _body(b).get("target", ""),
                                                                    _body(b).get("resolution", ""))), SMALL),
        "api/ingest/recheck": (lambda r, q, h, b: (200, {"job": app.start_check([_body(b).get("doc_id", "")])}),
                               SMALL),
        "api/billing/confirm": (lambda r, q, h, b: (200, app.billing_confirm(
            str(_body(b).get("doc_id", "")), _body(b).get("profile_id") or None)), SMALL),
        "api/question": (question, SMALL),
        "api/log": (lambda r, q, h, b: (200, app.log_entry(_body(b))), SMALL),
        "api/companion/enable": (lambda r, q, h, b: (200, app.companion_enable(bool(_body(b).get("on")))), SMALL),
        "api/companion/pair": (lambda r, q, h, b: (200, app.companion_pair()), SMALL),
        "api/companion/revoke": (lambda r, q, h, b: (200, app.companion_revoke(str(_body(b).get("pair_id", "")))), SMALL),
        "api/investigate": (lambda r, q, h, b: (200, {"job": app.investigate(_body(b).get("question", ""))}), SMALL),
        "api/bluetooth/scan": (lambda r, q, h, b: (200, {"job": app.bluetooth_scan(_body(b).get("seconds", 10))}), SMALL),
        "api/bluetooth/collect": (lambda r, q, h, b: (200, {"job": app.bluetooth_collect(
            _body(b).get("address", ""), _body(b).get("seconds", 60))}), SMALL),
    }
    return ({k: wrap(v) for k, v in get.items()}, {k: (wrap(fn), lim) for k, (fn, lim) in post.items()})


def make_server(app: App, dist: Path = None) -> HealthServer:
    dist = dist or runtime.ui_dir()
    demo = json.loads(runtime.demo_tracker().read_text(encoding="utf-8"))
    get, post = build_routes(app)

    def scenario():
        try:
            return app.scenario(), False
        except AppError as exc:
            raise RuntimeError(str(exc)) from exc

    return HealthServer(dist, scenario, lambda: (app.tracker(demo), True), app.set_setting,
                        get_routes=get, post_routes=post)


def start(kb: str = None):
    """Start the engine and the loopback UI server. Used by main() and the desktop launcher."""
    runtime.configure_tesseract()
    app = App(kb_path=Path(kb) if kb else None)
    server = make_server(app)
    url = server.start()
    app.companion_start_if_enabled()
    return app, server, url


def shutdown(app: App, server: HealthServer) -> None:
    """Stop everything this process started: phone listener, UI server, local AI, open vault."""
    try:
        if app._lan is not None:
            app._lan.stop()
            app._lan = None
    finally:
        try:
            server.stop()
        finally:
            app.close_models()
            app.lock_user()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    app, server, url = start(a.kb)
    print("PAi Health:", url)
    if app.kb:
        print(f"Knowledge base: {app.kb.meta.get('shards_included')} PubMed shards")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(app, server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
