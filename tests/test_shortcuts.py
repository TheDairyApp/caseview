import sys
from pathlib import Path

import pytest

from caseview.core import shortcuts

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")


def test_create_shortcut_runs_as_admin(tmp_path: Path) -> None:
    target = shortcuts.Target(sys.executable, "-m caseview tray", str(tmp_path), "")
    path = shortcuts.create(tmp_path / "CaseView.lnk", target)
    assert path.exists() and shortcuts.is_run_as_admin(path)
    shortcuts.remove(path)
    assert not path.exists()


def test_remove_refuses_other_files(tmp_path: Path) -> None:
    other = tmp_path / "Important.lnk"
    other.write_bytes(b"x")
    shortcuts.remove(other)
    assert other.exists()


def test_default_target_starts_the_tray() -> None:
    t = shortcuts.target()
    assert t.args.endswith("tray") and t.icon.endswith("caseview.ico")
