import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from caseview.core import autostart  # noqa: E402
from caseview.drivers.base import DisplayDriver  # noqa: E402
from caseview.rgb import output as rgb_output  # noqa: E402
from caseview.rgb.openrgb_client import OpenRgbError  # noqa: E402
from caseview.sensors.fake import FakeProvider  # noqa: E402
from caseview.transport.hid import HidError  # noqa: E402
from caseview.ui import tray  # noqa: E402


def _no_display() -> DisplayDriver:
    raise HidError("no display in tests")


def _offline(*a: object, **k: object) -> object:
    raise OpenRgbError("offline")


@pytest.fixture
def app_tray(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tray.TrayApp:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    real_output = rgb_output.RgbOutput
    monkeypatch.setattr(tray, "RgbOutput", lambda s: real_output(s, connect=_offline))
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    QApplication.instance() or QApplication([])
    t = tray.TrayApp(FakeProvider(), open_display_fn=_no_display)
    yield t
    t.quit()


def test_tray_menu_and_status_update(app_tray: tray.TrayApp) -> None:
    texts = [a.text() for a in app_tray.menu.actions()]
    for expected in ("Lighting…", "Lighting on", "Start with Windows", "Quit"):
        assert expected in texts
    app_tray.engine.step()
    app_tray.status = app_tray.engine.status
    app_tray._update_status()
    assert app_tray.state_action.text() == "Display: not connected"
    assert "CPU" in app_tray.values_action.text() and "GPU" in app_tray.values_action.text()


def test_lighting_toggle_saves_config(app_tray: tray.TrayApp, tmp_path: Path) -> None:
    app_tray.rgb_action.setChecked(True)
    text = (tmp_path / "CaseView" / "config.toml").read_text(encoding="utf-8")
    assert "enabled = true" in text and app_tray.rgb.settings.enabled


def test_lighting_window_shares_tray_output(app_tray: tray.TrayApp) -> None:
    app_tray.open_lighting()
    window = app_tray._window
    assert window is not None and window.output is app_tray.rgb
    window.close()
    QApplication.processEvents()
    assert app_tray.rgb._thread is not None  # shared connection kept alive


def test_icon_has_all_sizes() -> None:
    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QColor

    sizes = {s.width() for s in tray.make_icon(QColor(10, 132, 255)).availableSizes()}
    assert {16, 32, 64} <= sizes


def test_autostart_task_uses_highest_privileges(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "SUCCESS", "")

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    ok, _ = autostart.enable()
    assert ok
    cmd = calls[0]
    assert cmd[:3] == ["schtasks", "/create", "/tn"] and "highest" in cmd and "onlogon" in cmd
    assert cmd[cmd.index("/tr") + 1].endswith("-m caseview tray")
    autostart.disable()
    assert calls[1][:2] == ["schtasks", "/delete"]


def test_web_pages_round_trip_and_validation(app_tray: tray.TrayApp, tmp_path: Path) -> None:
    state = app_tray.web_state()["display"]
    assert [p["name"] for p in state["pages"]] == ["Power", "CPU", "GPU", "Temp"]
    pages = [
        {
            "name": "Mine",
            "enabled": True,
            "cpu_temp": "ram_pct",
            "cpu_fan": "gpu_clock",
            "gpu_temp": "gpu_load",
        }
    ]
    app_tray.web_set_pages(pages, 8)
    assert app_tray.engine.current_page().name == "Mine" and app_tray.config.page_seconds == 8
    assert 'name = "Mine"' in (tmp_path / "CaseView" / "config.toml").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        app_tray.web_set_pages([{**pages[0], "cpu_fan": "rm -rf"}], 8)
    with pytest.raises(ValueError):
        app_tray.web_set_pages([], 8)


def test_display_window_previews_live_and_reverts_without_save(app_tray: tray.TrayApp) -> None:
    app_tray.open_display_pages()
    window = app_tray._display_window
    assert window is not None
    window.cards[0].boxes["cpu_fan"].setCurrentIndex(
        window.cards[0].boxes["cpu_fan"].findData("cpu_clock")
    )
    assert app_tray.config.pages[0].cpu_fan == "cpu_clock"  # applied live
    window.close()
    assert app_tray.config.pages[0].cpu_fan == "total_power"  # reverted: not saved


def test_lock_page_from_tray_web_and_editor(app_tray: tray.TrayApp, tmp_path: Path) -> None:
    app_tray.lock_page("Temp")
    assert app_tray.engine.current_page().name == "Temp"
    assert 'locked_page = "Temp"' in (tmp_path / "CaseView" / "config.toml").read_text(
        encoding="utf-8"
    )
    assert app_tray.web_state()["display"]["locked"] == "Temp"
    with pytest.raises(ValueError):
        app_tray.web_lock_page("No such page")

    app_tray._fill_page_menu()
    checked = [a.text() for a in app_tray.page_menu.actions() if a.isChecked()]
    assert checked == ["Lock on “Temp”"]

    app_tray.open_display_pages()
    window = app_tray._display_window
    assert window is not None and window.cards[3].lock_button.isChecked()
    window.cards[0].lock_button.click()  # lock on Power from the editor
    assert app_tray.config.locked_page == "Power" and window.cards[0].lock_button.isChecked()
    window.unlock_button.click()
    assert app_tray.config.locked_page == "" and app_tray.config.locked() is None
    window.close()
