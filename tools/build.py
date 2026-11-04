"""Build the standalone Windows app into dist/CaseView/.

Run from the repo root (after `pip install -e .[build]` and `python -I tools/fetch_lhm.py`):

    python -I tools/build.py

Produces dist/CaseView/CaseView.exe (windowed; starts the tray app) plus its runtime
files, then removes Qt parts CaseView never loads to keep the folder small.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "CaseView"
LHM = ROOT / "vendor" / "lhm"

# Qt / Python modules CaseView doesn't use (PyInstaller would otherwise pull some in).
EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc",
    "pytest",
    "mypy",
    "ruff",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtMultimedia",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtXml",
]
# Files removed after the build (relative to the bundle's _internal folder).
PRUNE_GLOBS = [
    "PySide6/translations/*",
    "PySide6/opengl32sw.dll",  # 20 MB software OpenGL fallback; CaseView draws with QPainter
    "PySide6/Qt6Quick*.dll",
    "PySide6/Qt6Qml*.dll",
    "PySide6/Qt6Pdf*.dll",
    "PySide6/Qt6VirtualKeyboard*.dll",
    "PySide6/plugins/platforminputcontexts/*",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/imageformats/qtiff.dll",
    "PySide6/plugins/imageformats/qwebp.dll",
    "PySide6/plugins/imageformats/qgif.dll",
    "PySide6/plugins/imageformats/qtga.dll",
    "PySide6/plugins/imageformats/qwbmp.dll",
    "PySide6/plugins/imageformats/qicns.dll",
    "PySide6/plugins/generic/*",
    "PySide6/plugins/networkinformation/*",
    "PySide6/plugins/tls/*",
]


def folder_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def main() -> int:
    if not (LHM / "LibreHardwareMonitorLib.dll").exists():
        print("vendor/lhm missing: run `python -I tools/fetch_lhm.py` first", file=sys.stderr)
        return 1
    src = ROOT / "src" / "caseview"
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "CaseView",
        "--icon",
        str(src / "assets" / "caseview.ico"),
        "--add-data",
        f"{src / 'web' / 'index.html'};caseview/web",
        "--add-data",
        f"{src / 'assets'};caseview/assets",
        "--add-binary",
        f"{LHM};lhm",
        "--collect-submodules",
        "caseview",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
    ]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(str(ROOT / "tools" / "entry.py"))
    subprocess.run(args, check=True)

    internal = DIST / "_internal"
    before = folder_mb(DIST)
    for pattern in PRUNE_GLOBS:
        for path in internal.glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    print(f"dist/CaseView: {before:.0f} MB -> {folder_mb(DIST):.0f} MB after pruning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
