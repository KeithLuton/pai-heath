# PyInstaller spec for PAi Health (one-folder build; MSIX wraps the folder).
#   pyinstaller packaging\paihealth.spec --noconfirm --distpath build\pyi --workpath build\work
# Bundled tools come from build\resources (filled by build.ps1): llama\, tesseract\, knowledge\.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ENGINE = ROOT / "engine"
RES = Path(os.environ.get("PAIH_RESOURCES", ROOT / "build" / "resources"))
ICON = os.environ.get("PAIH_ICON", str(ROOT / "build" / "assets" / "PAiHealth.ico"))

ENGINE_PACKAGES = ["ai", "billing", "companion", "devices", "ingest", "investigate", "knowledge", "nutrition", "records", "review",
                   "spigot", "storage", "terminology", "tracker"]
hidden = ["app", "app_state", "runtime", "health_server"]
for pkg in ENGINE_PACKAGES:
    hidden += [m for m in collect_submodules(pkg) if ".tests" not in m and not m.startswith("tests")]
try:
    import bleak  # noqa: F401  optional Bluetooth LE
    hidden += collect_submodules("bleak")
except ImportError:
    pass

datas = [(str(ROOT / "dist"), "dist"),
         (str(ROOT / "src" / "data" / "tracker_demo.json"), os.path.join("src", "data")),
         (str(ENGINE / "terminology" / "map_loinc.csv"), "terminology"),
         (str(ENGINE / "terminology" / "map_rxnorm.csv"), "terminology")]
if RES.is_dir():
    datas.append((str(RES), "resources"))
notices = ROOT / "build" / "THIRD_PARTY_NOTICES.txt"
if notices.is_file():
    datas.append((str(notices), "."))

a = Analysis(
    [str(ENGINE / "desktop.py")],
    pathex=[str(ENGINE)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "tests", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6",
              "gi", "cefpython3", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PAiHealth",
    console=False,
    icon=ICON if os.path.isfile(ICON) else None,
    upx=False,                       # UPX-packed binaries trip antivirus and Store scans
    version=os.environ.get("PAIH_VERSION_FILE") or None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PAiHealth", upx=False)
