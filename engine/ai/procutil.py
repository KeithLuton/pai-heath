"""Keep helper processes from opening console windows.

PAi Health (ported from PAi Legal) shells out to PowerShell, nvidia-smi and llama-server.
On Windows each of those is a console application, so without these flags the
user gets a black window flashing over the app — once at startup, once per
capability refresh, and once per OCR'd page during an ingest.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def hidden() -> dict:
    """Popen/run keyword arguments that suppress a child console window."""
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW),
        "startupinfo": startup,
    }
