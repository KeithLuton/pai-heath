"""Conservative local hardware discovery for private model selection (ported from PAi Legal)."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .procutil import hidden


def app_data_root() -> Path:
    """Per-machine app data. PAI_HEALTH_HOME overrides it (tests, portable installs)."""
    override = os.environ.get("PAI_HEALTH_HOME")
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "PAiHealth"


@dataclass(frozen=True)
class HardwareProfile:
    total_ram_gb: float
    available_ram_gb: float
    free_disk_gb: float
    cpu_count: int
    architecture: str
    gpu_name: str = ""
    vram_gb: float = 0.0
    backend: str = "CPU"

    def as_dict(self) -> dict:
        return asdict(self)


def _memory() -> tuple[float, float]:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                        ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                        ("extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total / 2**30, status.available / 2**30
    try:
        import psutil  # type: ignore
        value = psutil.virtual_memory()
        return value.total / 2**30, value.available / 2**30
    except Exception:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        total = pages * size / 2**30
        try:
            avail = os.sysconf("SC_AVPHYS_PAGES") * size / 2**30
        except (ValueError, OSError):
            avail = total * 0.65
        return total, avail


def _gpu() -> tuple[str, float, str]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, **hidden(),
        )
        if result.returncode == 0 and result.stdout.strip():
            name, memory = [part.strip() for part in result.stdout.splitlines()[0].rsplit(",", 1)]
            return name, float(memory) / 1024, "CUDA"
    except Exception:
        pass
    if os.name == "nt":
        script = "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=12, **hidden())
            data = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
            items = data if isinstance(data, list) else [data]
            best = max(items, key=lambda item: int(item.get("AdapterRAM") or 0), default={})
            return str(best.get("Name") or ""), int(best.get("AdapterRAM") or 0) / 2**30, "GPU" if best else "CPU"
        except Exception:
            pass
    return "", 0.0, "CPU"


def scan_hardware(root: Path | None = None) -> HardwareProfile:
    total, available = _memory()
    destination = root or app_data_root()
    destination.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(destination).free / 2**30
    gpu_name, vram, backend = _gpu()
    return HardwareProfile(round(total, 1), round(available, 1), round(free, 1), os.cpu_count() or 1, platform.machine(), gpu_name, round(vram, 1), backend)
