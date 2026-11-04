"""Desktop and Start-menu shortcuts that start the tray app with administrator rights.

Shortcuts are written through the Windows Script Host COM object (no extra
dependencies), then flagged "Run as administrator" so CPU and motherboard
sensors work. Windows shows its normal UAC prompt when you open them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

NAME = "CaseView"
_RUNAS_BYTE, _RUNAS_FLAG = 0x15, 0x20  # ShellLink header: LinkFlags bit 13 (RunAsUser)


@dataclass(frozen=True)
class Target:
    exe: str
    args: str
    workdir: str
    icon: str


def target() -> Target:
    """What a shortcut should launch: the built exe, or pythonw -m caseview tray."""
    icon = str(resources.files("caseview.assets").joinpath("caseview.ico"))
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return Target(str(exe), "tray", str(exe.parent), icon)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return Target(str(runner), "-m caseview tray", str(Path.cwd()), icon)


def _folder(kind: str) -> Path:
    """Known folders via .NET, so a OneDrive-redirected Desktop is handled."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{kind}')"],
        capture_output=True,
        text=True,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    return Path(out)


def desktop_path() -> Path:
    return _folder("Desktop") / f"{NAME}.lnk"


def start_menu_path() -> Path:
    return _folder("Programs") / f"{NAME}.lnk"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def create(path: Path, t: Target | None = None) -> Path:
    if sys.platform != "win32":
        raise OSError("shortcuts are only supported on Windows")
    t = t or target()
    path.parent.mkdir(parents=True, exist_ok=True)
    script = "; ".join(
        [
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(path))})",
            f"$s.TargetPath = {_ps_quote(t.exe)}",
            f"$s.Arguments = {_ps_quote(t.args)}",
            f"$s.WorkingDirectory = {_ps_quote(t.workdir)}",
            f"$s.IconLocation = {_ps_quote(t.icon + ',0')}",
            "$s.Description = 'CaseView: case display and lighting'",
            "$s.Save()",
        ]
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    set_run_as_admin(path)
    return path


def set_run_as_admin(path: Path) -> None:
    data = bytearray(path.read_bytes())
    if len(data) <= _RUNAS_BYTE or data[:4] != b"\x4c\x00\x00\x00":
        raise ValueError(f"{path} is not a shell link")
    data[_RUNAS_BYTE] |= _RUNAS_FLAG
    path.write_bytes(bytes(data))


def is_run_as_admin(path: Path) -> bool:
    data = path.read_bytes()
    return len(data) > _RUNAS_BYTE and bool(data[_RUNAS_BYTE] & _RUNAS_FLAG)


def remove(path: Path) -> None:
    if path.exists() and path.suffix.lower() == ".lnk" and path.stem == NAME:
        os.remove(path)
