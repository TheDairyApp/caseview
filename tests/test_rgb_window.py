import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from caseview.rgb.effects import Effect, RgbSettings  # noqa: E402
from caseview.rgb.openrgb_client import OpenRgbError  # noqa: E402
from caseview.rgb.output import RgbOutput  # noqa: E402
from caseview.sensors.fake import FakeProvider  # noqa: E402
from caseview.ui import rgb_window  # noqa: E402


def _offline(*args: object, **kwargs: object) -> object:
    raise OpenRgbError("offline in tests")


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rgb_window.RgbWindow:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(rgb_window, "OpenRgbClient", _offline)
    monkeypatch.setattr(
        rgb_window,
        "RgbOutput",
        lambda s: RgbOutput(s, connect=_offline),  # type: ignore[arg-type]
    )
    QApplication.instance() or QApplication([])
    w = rgb_window.RgbWindow(FakeProvider())
    yield w
    w.close()


def test_window_builds_and_reports_offline_openrgb(window: rgb_window.RgbWindow) -> None:
    assert "offline" in window.status.text()
    assert window.zone_list.count() == 1  # "All zones" only without a device


def test_picking_a_color_updates_settings_and_switches_to_static(
    window: rgb_window.RgbWindow,
) -> None:
    window.hex_edit.setText("#00FF80")
    window._hex_changed()
    assert window.settings.color == (0, 255, 128)
    assert window.settings.effect is Effect.STATIC
    assert [s.value() for s in window.rgb_spins] == [0, 255, 128]


def test_effect_controls_feed_settings(window: rgb_window.RgbWindow) -> None:
    window.effect_box.setCurrentIndex(window.effect_box.findData(Effect.RAINBOW))
    window.brightness.setValue(40)
    window.speed.setValue(9)
    window.alert_on.setChecked(False)
    s = window.settings
    assert (s.effect, s.brightness, s.speed, s.alert_temp) == (Effect.RAINBOW, 40, 9, 0.0)


def test_save_as_startup_writes_config(
    window: rgb_window.RgbWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rgb_window.QMessageBox, "information", lambda *a: None)
    window.settings = RgbSettings(enabled=True, effect=Effect.BREATHING, color=(1, 2, 3))
    window._save_default()
    text = (tmp_path / "CaseView" / "config.toml").read_text(encoding="utf-8")
    assert 'effect = "breathing"' in text and '"#010203"' in text


def test_temperature_steps_editor(window: rgb_window.RgbWindow) -> None:
    window._reset_steps()
    assert len(window._step_rows) == 6
    window._add_step()
    assert len(window._step_rows) == 7 and window.settings.temp_stops[-1][0] == 100.0
    window._remove_step(window._step_rows[0][0])
    assert len(window.settings.temp_stops) == 6
    window.blend.setChecked(False)
    assert window.settings.temp_blend is False


def test_theme_presets_save_load_and_broken_file(tmp_path: Path) -> None:
    from caseview.ui.theme import PRESETS, Theme, load_theme, save_theme, stylesheet

    path = save_theme(PRESETS["Aurora"], tmp_path / "theme.toml")
    assert load_theme(path) == PRESETS["Aurora"]
    path.write_text('[theme]\nname = "Gone"', encoding="utf-8")
    assert load_theme(path) == Theme()
    path.write_text("this is [ not toml", encoding="utf-8")
    assert load_theme(path) == Theme()
    for theme in PRESETS.values():
        assert "QFrame#Card" in stylesheet(theme) and not theme.blur


def test_theme_dropdown_applies_and_saves(window: rgb_window.RgbWindow, tmp_path: Path) -> None:
    window.theme_box.setCurrentText("Light")
    assert window.theme.name == "Light"
    assert 'name = "Light"' in (tmp_path / "CaseView" / "theme.toml").read_text(encoding="utf-8")


def test_custom_painted_buttons_are_not_styled_as_push_buttons() -> None:
    from PySide6.QtWidgets import QPushButton

    from caseview.ui.widgets import ColorChip, Swatch

    assert not issubclass(Swatch, QPushButton) and not issubclass(ColorChip, QPushButton)
