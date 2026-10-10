"""Where things are when PAi Health runs from source, from a PyInstaller folder, or from
the Microsoft Store (MSIX).

Install folder (read-only under MSIX): the UI (dist/), bundled tools (resources/llama,
resources/tesseract) and an optional bundled knowledge base (resources/knowledge).

Per-user data stays under app_data_root() (%LOCALAPPDATA%\\PAiHealth). Under MSIX, Windows
redirects that folder into the package's private LocalCache: it is not roamed, and it is
DELETED when the app is uninstalled. That is why backups go to Documents (see backup_dir).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

SOURCE_ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = "1.0.0"
NOTICE_VERSION = "PAIH-DATA-NOTICE-v1"
COMPANION_PORTS = range(47110, 47120)       # must match the firewall rule in packaging/AppxManifest.xml


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """Folder holding dist/ and resources/ (PyInstaller puts data under _MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))


def ui_dir() -> Path:
    return install_root() / "dist"


def demo_tracker() -> Path:
    return install_root() / "src" / "data" / "tracker_demo.json"


def resources() -> Path:
    return install_root() / "resources"


def packaged() -> bool:
    """True inside an MSIX package (has package identity)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return rc != 15700            # APPMODEL_ERROR_NO_PACKAGE
    except Exception:
        return False


def bundled_kb() -> Optional[Path]:
    found = sorted((resources() / "knowledge").glob("psi_health_kb*.db"))
    return found[-1] if found else None


def configure_tesseract() -> Optional[Path]:
    """Point pytesseract at the bundled Tesseract, if there is one. Returns the exe path."""
    exe = resources() / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")
    if not exe.is_file():
        return None
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = str(exe)
    except ImportError:
        return None
    os.environ.setdefault("TESSDATA_PREFIX", str(exe.parent / "tessdata"))
    return exe


def documents_dir() -> Path:
    """The user's Documents folder (honours OneDrive/folder redirection on Windows)."""
    override = os.environ.get("PAI_HEALTH_DOCUMENTS")
    if override:
        return Path(override)
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:   # CSIDL_PERSONAL
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def backup_dir() -> Path:
    """Outside the package, so backups survive uninstalling the app. Files are encrypted."""
    return documents_dir() / "PAi Health Backups"
