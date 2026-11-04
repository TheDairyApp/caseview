"""Entry point for the packaged CaseView.exe (PyInstaller, windowed).

A windowed exe has no console, so sys.stdout/stderr can be None; send them to the
null device so CLI messages can't crash the app. Redirected output (e.g. from a
script) keeps working.
"""

import os
import sys

for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115

from caseview.cli import main  # noqa: E402

raise SystemExit(main())
