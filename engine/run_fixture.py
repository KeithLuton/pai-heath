"""Development launcher: serves the built UI with the fixture as engine output.

Everything it serves is marked synthetic. The real engine replaces the two
providers and the settings sink; the server does not change.
"""
from __future__ import annotations

import json
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from health_server import HealthServer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "data"
settings_log: list[dict] = []


def scenario():
    return json.loads((DATA / "scenario_001.json").read_text(encoding="utf-8")), True


def tracker():
    data = json.loads((DATA / "tracker_demo.json").read_text(encoding="utf-8"))
    for entry in settings_log:
        data["settings"][entry["key"]] = entry["value"]
    return data, True


def on_setting(key, value):
    if key == "data_path" and value == "LIVE":
        raise ValueError("Live mode needs the phone companion, which isn't available yet")
    settings_log.append({"key": key, "value": value, "at": datetime.now().isoformat(timespec="seconds")})


if __name__ == "__main__":
    server = HealthServer(ROOT / "dist", scenario, tracker, on_setting)
    url = server.start()
    print("PAi Health (fixture mode):", url)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
