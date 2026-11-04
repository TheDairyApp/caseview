"""Rotating log file in %APPDATA%\\CaseView\\logs, plus console output for the CLI."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from caseview.core.config import default_config_path


def log_dir() -> Path:
    return default_config_path().parent / "logs"


def setup(verbose: bool = False) -> Path:
    path = log_dir() / "caseview.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    console.setLevel(logging.INFO)
    root.handlers[:] = [file_handler, console]
    return path
