"""Start CaseView at logon through Task Scheduler with highest privileges.

A scheduled task (not the Run registry key) is used because CPU and motherboard
sensors need administrator rights, and a task set to "run with highest privileges"
starts elevated without a UAC prompt at every boot. Creating or deleting the task
itself needs administrator rights once.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "CaseView"


def launch_command() -> str:
    """Command line Task Scheduler runs: the frozen exe, or pythonw -m caseview tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" tray'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return f'"{runner}" -m caseview tray'


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def is_enabled() -> bool:
    return sys.platform == "win32" and _run(["/query", "/tn", TASK_NAME]).returncode == 0


def enable() -> tuple[bool, str]:
    result = _run(
        [
            "/create",
            "/tn",
            TASK_NAME,
            "/tr",
            launch_command(),
            "/sc",
            "onlogon",
            "/rl",
            "highest",
            "/f",
        ]
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def disable() -> tuple[bool, str]:
    result = _run(["/delete", "/tn", TASK_NAME, "/f"])
    return result.returncode == 0, (result.stderr or result.stdout).strip()
