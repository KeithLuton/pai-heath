"""PAi Health desktop entry point (what the Store package launches).

Opens the loopback UI in its own WebView2 window — not the user's browser, so browser
extensions never see health data and nothing is left in browser history. Closing the window
stops everything: the phone listener, any private AI process, the UI server, and locks the vault.

    PAiHealth.exe            (packaged)
    python engine\\desktop.py (from source; needs: pip install pywebview)
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime                                   # noqa: E402
from ai.hardware import app_data_root            # noqa: E402

TITLE = "PAi Health"
WEBVIEW2_CLIENT = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


# ---------------------------------------------------------------- helpers
def message(text: str, error: bool = False) -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)
    else:
        print(text, file=sys.stderr)


class SingleInstance:
    """An exclusive lock on a file in the app data folder. Two copies would fight over the vaults."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


def webview2_installed() -> bool:
    if os.name != "nt":
        return True
    import winreg
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + WEBVIEW2_CLIENT[8:]),
                      (winreg.HKEY_LOCAL_MACHINE, WEBVIEW2_CLIENT),
                      (winreg.HKEY_CURRENT_USER, WEBVIEW2_CLIENT)):
        try:
            with winreg.OpenKey(hive, key) as k:
                version, _ = winreg.QueryValueEx(k, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def crash_log(exc: BaseException) -> Path:
    """Traceback only; never record content. Kept locally for the user to send if they choose."""
    path = app_data_root() / "logs" / "desktop-error.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
    return path


# ---------------------------------------------------------------- main
def selftest() -> int:
    """Packaged-build check (no window): engine starts, UI and API answer, OCR and AI runtime found."""
    import json
    import urllib.request
    from ai.local_ai import find_runtime
    from app import shutdown, start
    app, server, url = start()
    try:
        ok_ui = urllib.request.urlopen(url, timeout=10).status == 200
        about = json.loads(urllib.request.urlopen(url + "api/about", timeout=10).read())
        state = json.loads(urllib.request.urlopen(url + "api/app", timeout=10).read())
        report = {"ui": ok_ui, "version": about["version"], "ocr": about["ocr"],
                  "llama_server": str(find_runtime() or ""), "knowledge_base": state["knowledge_base"],
                  "webview2": webview2_installed()}
    finally:
        shutdown(app, server)
    out = json.dumps(report, indent=2)
    print(out)
    (app_data_root() / "logs").mkdir(parents=True, exist_ok=True)
    (app_data_root() / "logs" / "selftest.json").write_text(out, encoding="utf-8")
    return 0 if ok_ui else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    lock = SingleInstance(app_data_root() / "paihealth.lock")
    if not lock.acquire():
        message("PAi Health is already open.")
        return 0
    if not webview2_installed():
        message("PAi Health needs the Microsoft Edge WebView2 Runtime, which is part of Windows 11 and "
                f"current Windows 10. Install it from {WEBVIEW2_URL} and open PAi Health again.", error=True)
        lock.release()
        return 1
    import webview                                # pywebview (BSD-3)
    from app import shutdown, start

    app = server = None
    try:
        app, server, url = start()
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True   # provider key pages, PubMed links
        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.create_window(TITLE, url, width=1320, height=880, min_size=(980, 640),
                                       background_color="#F7F8FA", text_select=True, zoomable=True)
        webview.start(gui="edgechromium", private_mode=True, debug=False)
        return 0
    except Exception as exc:                       # noqa: BLE001
        path = crash_log(exc)
        message(f"PAi Health hit an error and has to close.\n\n{exc}\n\nDetails were saved to:\n{path}",
                error=True)
        return 1
    finally:
        if app is not None and server is not None:
            shutdown(app, server)
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
