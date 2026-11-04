"""Render the CaseView icon (same drawing as the tray icon) to src/caseview/assets/.

Run: python -I tools/make_icon.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402

from caseview.ui.tray import make_icon  # noqa: E402


def main() -> int:
    QGuiApplication([])
    out = ROOT / "src" / "caseview" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    icon = make_icon(QColor(10, 132, 255), sizes=(16, 24, 32, 48, 64, 128, 256))
    icon.pixmap(256, 256).save(str(out / "caseview.png"))
    ok = icon.pixmap(256, 256).toImage().save(str(out / "caseview.ico"), "ICO")
    print("wrote", out, "ico ok" if ok else "ICO writer unavailable")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
