"""Detect (and optionally stop) vendor apps that also write to the display."""

from __future__ import annotations

import csv
import io
import subprocess
import sys

# Lower-case process image names.
CONFLICTING_APPS = {"lcd-cs.exe": "DT Control"}


def running_conflicts() -> list[str]:
    """Image names of running conflicting apps, e.g. ["LCD-CS.exe"]."""
    if sys.platform != "win32":
        return []
    out = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    rows = csv.reader(io.StringIO(out))
    return sorted({row[0] for row in rows if row and row[0].lower() in CONFLICTING_APPS})


def describe(image: str) -> str:
    return f"{CONFLICTING_APPS.get(image.lower(), image)} ({image})"


def stop_app(image: str) -> bool:
    """Ask Windows to end the app. Needs admin if the app runs elevated (DT Control does)."""
    if image.lower() not in CONFLICTING_APPS:
        raise ValueError(f"refusing to stop unknown process {image!r}")
    result = subprocess.run(
        ["taskkill", "/im", image, "/f"],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0
