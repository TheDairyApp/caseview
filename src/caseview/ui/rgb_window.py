"""Lighting window: color wheel, per-zone colors, effects, temperature steps and profiles.

Layout: header (title, device, status, theme) / live LED preview / three columns
(zones, color, effect settings) / footer (profiles, save). Sensors are read on a
background thread so the window never stalls while dragging.
"""

from __future__ import annotations

import colorsys
import copy
import logging
import sys
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from caseview.core import config as config_mod
from caseview.core.config import ConfigError
from caseview.rgb import profiles
from caseview.rgb.effects import (
    DEFAULT_TEMP_STOPS,
    Color,
    Effect,
    RgbSettings,
    TempStop,
    compute_leds,
    format_color,
    parse_color,
)
from caseview.rgb.openrgb_client import Controller, OpenRgbClient, OpenRgbError, Zone
from caseview.rgb.output import RgbOutput
from caseview.sensors import SensorProvider, pick_values
from caseview.ui.theme import PRESETS as THEMES
from caseview.ui.theme import Theme, apply_theme, load_theme, save_theme
from caseview.ui.widgets import (
    ColorChip,
    ColorWheel,
    LedStrip,
    NumberField,
    Swatch,
    TempGradient,
    ToggleSwitch,
    ValueSlider,
)

log = logging.getLogger(__name__)

ALL_ZONES = "All zones"
EFFECT_LABELS = {
    Effect.STATIC: "Static color",
    Effect.TEMPERATURE: "Temperature",
    Effect.BREATHING: "Breathing",
    Effect.COLOR_CYCLE: "Color cycle",
    Effect.RAINBOW: "Rainbow wave",
    Effect.OFF: "Off",
}
SOURCES = {"cpu_temp": "CPU temperature", "gpu_temp": "GPU temperature"}
PRESETS: list[Color] = [
    (255, 59, 48),
    (255, 149, 0),
    (255, 204, 0),
    (52, 199, 89),
    (0, 199, 190),
    (50, 173, 230),
    (0, 122, 255),
    (88, 86, 214),
    (175, 82, 222),
    (255, 45, 85),
    (255, 255, 255),
    (0, 0, 0),
]
SPACING = 12


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(10)
    if title:
        label = QLabel(title)
        label.setObjectName("CardTitle")
        layout.addWidget(label)
    return frame, layout


def field_row(label: str, widget: QWidget | QLayout, label_width: int = 86) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(10)
    text = QLabel(label)
    text.setObjectName("Muted")
    text.setFixedWidth(label_width)
    row.addWidget(text)
    if isinstance(widget, QLayout):
        row.addLayout(widget, 1)
    else:
        row.addWidget(widget, 1)
    return row


class RgbWindow(QWidget):
    """Standalone (own sensors + own RGB connection) or hosted by the tray.

    Hosted: pass the tray engine's `output` and a `values` callable. Edits drive the
    engine's lights live; closing without "Save as startup lighting" restores the
    saved lighting, and the shared connection stays open.
    """

    def __init__(
        self,
        provider: SensorProvider | None,
        output: RgbOutput | None = None,
        values: Callable[[], dict[str, float]] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("CaseView – Lighting")
        self.setMinimumSize(900, 600)
        self.resize(1180, 760)
        self._provider = provider
        self._values: dict[str, float] = {}
        self._syncing = False
        self._zones: list[tuple[str, int]] = []
        self._step_rows: list[tuple[QWidget, NumberField, ColorChip]] = []

        cfg = config_mod.load()
        self.settings: RgbSettings = copy.deepcopy(cfg.rgb)
        self.settings.enabled = True
        self._saved_rgb = copy.deepcopy(cfg.rgb)
        self._shared = output is not None
        self._values_source = values
        if output is None:
            output = RgbOutput(copy.deepcopy(self.settings))
            output.start()
        self.output = output

        self._preview = QTimer(self)
        self._preview.timeout.connect(self._tick_preview)
        self.theme = load_theme()
        self._build()
        self._apply_theme(self.theme)
        self._load_devices()
        self._sync_from_settings()
        self._preview.start()

        self._stop_sensors = threading.Event()
        if provider is not None:
            threading.Thread(target=self._sensor_loop, name="sensors", daemon=True).start()

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(SPACING)

        # header
        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Lighting")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addSpacing(12)
        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(220)
        self.device_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.device_box.setMaximumWidth(420)
        self.device_box.currentIndexChanged.connect(self._device_changed)
        header.addWidget(self.device_box)
        refresh = QPushButton("⟳")
        refresh.setObjectName("Icon")
        refresh.setToolTip("Look for OpenRGB devices again")
        refresh.clicked.connect(self._load_devices)
        header.addWidget(refresh)
        header.addStretch()
        self.status = QLabel()
        self.status.setObjectName("Pill")
        header.addWidget(self.status)
        self.theme_box = QComboBox()
        self.theme_box.addItems(list(THEMES))
        self.theme_box.setToolTip("Theme")
        self.theme_box.currentTextChanged.connect(self._theme_picked)
        header.addWidget(self.theme_box)
        root.addLayout(header)

        self.strip = LedStrip()
        root.addWidget(self.strip)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        root.addLayout(body, 1)
        # Columns share extra width 2 : 5 : 3, within sensible min/max widths.
        body.addWidget(self._build_zones(), 2)
        body.addWidget(self._build_color(), 5)
        body.addWidget(self._build_settings(), 3)

        root.addLayout(self._build_footer())

    def _build_zones(self) -> QFrame:
        frame, layout = card("Zones")
        frame.setMinimumWidth(180)
        frame.setMaximumWidth(280)
        self.zone_list = QListWidget()
        self.zone_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.zone_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.zone_list.currentTextChanged.connect(self._zone_changed)
        layout.addWidget(self.zone_list, 1)
        hint = QLabel("Pick a zone to give it its own color (Static effect).")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        reset = QPushButton("One color for all")
        reset.setToolTip("Remove per-zone colors")
        reset.clicked.connect(self._reset_zones)
        layout.addWidget(reset)
        return frame

    def _build_color(self) -> QFrame:
        frame, layout = card("Color")
        self.wheel = ColorWheel()  # paints a centered circle sized to fit, up to 520 px
        self.wheel.hueSatChanged.connect(self._wheel_changed)
        layout.addWidget(self.wheel, 1)

        self.value_slider = ValueSlider(0, 100, " %")
        self.value_slider.valueChanged.connect(self._value_changed)
        layout.addLayout(field_row("Brightness", self.value_slider, 70))

        # Hex group and RGB group sit side by side, or stack when the card is narrow.
        self.hex_group = QWidget()
        hex_row = QHBoxLayout(self.hex_group)
        hex_row.setContentsMargins(0, 0, 0, 0)
        hex_row.setSpacing(8)
        self.current_chip = ColorChip((0, 0, 0))
        self.current_chip.setEnabled(False)
        hex_row.addWidget(self.current_chip)
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setFixedWidth(96)
        self.hex_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hex_edit.editingFinished.connect(self._hex_changed)
        hex_row.addWidget(self.hex_edit)
        self.rgb_group = QWidget()
        rgb_row = QHBoxLayout(self.rgb_group)
        rgb_row.setContentsMargins(0, 0, 0, 0)
        rgb_row.setSpacing(6)
        self.rgb_spins: list[NumberField] = []
        for label in "RGB":
            tag = QLabel(label)
            tag.setObjectName("Muted")
            rgb_row.addWidget(tag)
            spin = NumberField(0, 255, width=52)
            spin.valueChanged.connect(self._spins_changed)
            rgb_row.addWidget(spin)
            self.rgb_spins.append(spin)
        self.inputs = QGridLayout()
        self.inputs.setHorizontalSpacing(12)
        self.inputs.setVerticalSpacing(8)
        self.inputs.setColumnStretch(1, 1)
        self._inputs_wide: bool | None = None
        layout.addLayout(self.inputs)

        divider = QFrame()
        divider.setObjectName("Divider")
        layout.addWidget(divider)
        self.swatch_grid = QGridLayout()
        self.swatch_grid.setHorizontalSpacing(6)
        self.swatch_grid.setVerticalSpacing(6)
        self.swatches: list[Swatch] = []
        for c in PRESETS:
            sw = Swatch(c)
            sw.picked.connect(self._swatch_picked)
            self.swatches.append(sw)
        self._swatch_cols = 0
        swatch_row = QHBoxLayout()
        swatch_row.addStretch()
        swatch_row.addLayout(self.swatch_grid)
        swatch_row.addStretch()
        layout.addLayout(swatch_row)
        self.color_card = frame
        self._layout_swatches()
        return frame

    def _layout_swatches(self) -> None:
        """Reflow the color card for its current width."""
        width = self.color_card.width()
        wide = width >= self.hex_group.sizeHint().width() + self.rgb_group.sizeHint().width() + 60
        if wide != self._inputs_wide:
            self._inputs_wide = wide
            self.inputs.addWidget(self.hex_group, 0, 0)
            if wide:
                self.inputs.addWidget(self.rgb_group, 0, 2)
            else:
                self.inputs.addWidget(self.rgb_group, 1, 0, 1, 3)
        cols = 12 if width >= 12 * 36 + 40 else 6
        if cols != self._swatch_cols:
            self._swatch_cols = cols
            for i, sw in enumerate(self.swatches):
                self.swatch_grid.addWidget(sw, i // cols, i % cols)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_swatches()

    def _build_settings(self) -> QScrollArea:
        column = QWidget()
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 4, 0)
        col.setSpacing(SPACING)

        fx, fl = card("Effect")
        self.effect_box = QComboBox()
        for effect, label in EFFECT_LABELS.items():
            self.effect_box.addItem(label, effect)
        self.effect_box.currentIndexChanged.connect(self._changed)
        fl.addLayout(field_row("Mode", self.effect_box))
        self.brightness = ValueSlider(0, 100, " %")
        self.brightness.valueChanged.connect(self._changed)
        fl.addLayout(field_row("Brightness", self.brightness))
        self.speed = ValueSlider(1, 10)
        self.speed.valueChanged.connect(self._changed)
        self.speed_row = field_row("Speed", self.speed)
        fl.addLayout(self.speed_row)
        col.addWidget(fx)

        temp, tl = card("Temperature colors")
        self.source = QComboBox()
        for key, label in SOURCES.items():
            self.source.addItem(label, key)
        self.source.currentIndexChanged.connect(self._changed)
        tl.addLayout(field_row("Sensor", self.source))
        self.sensor_label = QLabel("–")
        tl.addLayout(field_row("Now", self.sensor_label))
        self.gradient = TempGradient()
        tl.addWidget(self.gradient)
        self.steps_layout = QVBoxLayout()
        self.steps_layout.setSpacing(6)
        tl.addLayout(self.steps_layout)
        buttons = QHBoxLayout()
        add_step = QPushButton("+  Add step")
        add_step.setObjectName("Ghost")
        add_step.clicked.connect(self._add_step)
        reset_steps = QPushButton("Reset")
        reset_steps.setObjectName("Ghost")
        reset_steps.setToolTip("Blue, light blue, green, yellow, orange, red")
        reset_steps.clicked.connect(self._reset_steps)
        buttons.addWidget(add_step)
        buttons.addStretch()
        buttons.addWidget(reset_steps)
        tl.addLayout(buttons)
        self.blend = ToggleSwitch("Blend between steps")
        self.blend.setToolTip("Off: one solid color per band, e.g. 60–70 °C stays yellow")
        self.blend.toggled.connect(self._changed)
        tl.addWidget(self.blend)
        col.addWidget(temp)

        alert, al = card("Overheat alert")
        self.alert_on = ToggleSwitch("Flash when too hot")
        self.alert_on.toggled.connect(self._changed)
        al.addWidget(self.alert_on)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.alert_temp = NumberField(30, 120, " °C", 76)
        self.alert_temp.valueChanged.connect(self._changed)
        self.alert_color = ColorChip((255, 0, 0))
        self.alert_color.colorChanged.connect(self._changed)
        row.addWidget(self.alert_temp)
        row.addWidget(self.alert_color)
        row.addStretch()
        al.addLayout(field_row("At", row, 86))
        col.addWidget(alert)
        col.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(column)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(460)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)
        label = QLabel("Profile")
        label.setObjectName("Muted")
        footer.addWidget(label)
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(200)
        self.profile_box.setPlaceholderText("No saved profiles")
        footer.addWidget(self.profile_box)
        for text, slot in (
            ("Load", self._load_profile),
            ("Save as…", self._save_profile),
            ("Delete", self._delete_profile),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            footer.addWidget(b)
        footer.addStretch()
        save_default = QPushButton("Save as startup lighting")
        save_default.setObjectName("Primary")
        save_default.setToolTip("Used by `caseview run` and the tray app")
        save_default.clicked.connect(self._save_default)
        footer.addWidget(save_default)
        self._refresh_profiles()
        return footer

    # --------------------------------------------------------------- theme

    def _apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        apply_theme(self, theme)
        self.theme_box.blockSignals(True)
        self.theme_box.setCurrentText(theme.name)
        self.theme_box.blockSignals(False)
        for w in (self.wheel, self.strip, self.gradient):
            w.set_radius(theme.radius)

    def _theme_picked(self, name: str) -> None:
        if name in THEMES and name != self.theme.name:
            self._apply_theme(THEMES[name])
            save_theme(self.theme)

    # ---------------------------------------------------------- temp steps

    def _set_steps(self, stops: list[TempStop]) -> None:
        for row, _, _ in self._step_rows:
            row.setParent(None)
            row.deleteLater()
        self._step_rows = []
        for temp, color in sorted(stops):
            self._append_step_row(temp, color)

    def _append_step_row(self, temp: float, color: Color) -> None:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        chip = ColorChip(color)
        chip.colorChanged.connect(self._changed)
        label = QLabel("from")
        label.setObjectName("Muted")
        spin = NumberField(0, 120, " °C", 76)
        spin.setValue(temp)
        spin.valueChanged.connect(self._changed)
        remove = QPushButton("✕")
        remove.setObjectName("Icon")
        remove.setToolTip("Remove this step")
        remove.clicked.connect(lambda: self._remove_step(row))
        h.addWidget(chip)
        h.addWidget(label)
        h.addWidget(spin)
        h.addStretch()
        h.addWidget(remove)
        self.steps_layout.addWidget(row)
        self._step_rows.append((row, spin, chip))

    def _steps(self) -> list[TempStop]:
        return sorted((float(spin.value()), chip.color) for _, spin, chip in self._step_rows)

    def _add_step(self) -> None:
        stops = self._steps()
        temp = min(stops[-1][0] + 10, 120) if stops else 50.0
        color = stops[-1][1] if stops else (0, 80, 255)
        self._append_step_row(temp, color)
        self._changed()

    def _remove_step(self, row: QWidget) -> None:
        if len(self._step_rows) <= 1:
            return  # keep at least one step
        self._step_rows = [r for r in self._step_rows if r[0] is not row]
        row.setParent(None)
        row.deleteLater()
        self._changed()

    def _reset_steps(self) -> None:
        self._set_steps(list(DEFAULT_TEMP_STOPS))
        self._changed()

    # ------------------------------------------------------------- devices

    def _load_devices(self) -> None:
        self.device_box.blockSignals(True)
        self.device_box.clear()
        try:
            client = OpenRgbClient(name="CaseView-ui")
            controllers = client.controllers()
            client.close()
        except OpenRgbError as e:
            self._set_status(str(e), ok=False)
            controllers = []
        for c in controllers:
            self.device_box.addItem(f"{c.name}  ·  {c.led_count} LEDs", c.name)
        wanted = self.settings.device.lower()
        for i, c in enumerate(controllers):
            if wanted and wanted in c.name.lower():
                self.device_box.setCurrentIndex(i)
                break
        self.device_box.blockSignals(False)
        self._fill_zones([(z.name, z.led_count) for z in self._selected_zones(controllers)])

    def _selected_zones(self, controllers: list[Controller]) -> list[Zone]:
        i = self.device_box.currentIndex()
        return controllers[i].zones if 0 <= i < len(controllers) else []

    def _device_changed(self) -> None:
        name = self.device_box.currentData()
        if name:
            self.settings.device = str(name)
            self._push()
            self._load_devices()

    def _fill_zones(self, zones: list[tuple[str, int]]) -> None:
        self._zones = zones
        current = self.zone_list.currentItem()
        keep = self._zone_name(current.text()) if current else ALL_ZONES
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        self.zone_list.addItem(ALL_ZONES)
        for name, count in zones:
            self.zone_list.addItem(f"{name}    {count} LED{'s' if count != 1 else ''}")
        names = [ALL_ZONES] + [n for n, _ in zones]
        self.zone_list.setCurrentRow(names.index(keep) if keep in names else 0)
        self.zone_list.blockSignals(False)

    @staticmethod
    def _zone_name(text: str) -> str:
        return text.split("    ")[0]

    # -------------------------------------------------------------- colors

    def _zone(self) -> str:
        item = self.zone_list.currentItem()
        return self._zone_name(item.text()) if item else ALL_ZONES

    def _edited_color(self) -> Color:
        return self.settings.zone_colors.get(self._zone(), self.settings.color)

    def _set_edited_color(self, c: Color) -> None:
        zone = self._zone()
        if zone == ALL_ZONES:
            self.settings.color = c
            if self.settings.effect not in (Effect.STATIC, Effect.BREATHING):
                self._select_effect(Effect.STATIC)  # make the picked color visible
        else:
            self.settings.zone_colors[zone] = c
            if self.settings.effect is not Effect.STATIC:
                self._select_effect(Effect.STATIC)  # per-zone colors only apply to Static
        self._show_color(c)
        self._push()

    def _show_color(self, c: Color) -> None:
        self._syncing = True
        h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
        self.wheel.set_hue_sat(h, s, QColor(*c))
        self.value_slider.setValue(round(v * 100))
        self.hex_edit.setText(format_color(c))
        self.current_chip.set_color(c)
        for spin, x in zip(self.rgb_spins, c, strict=True):
            spin.setValue(x)
        for sw in self.swatches:
            sw.set_selected(sw.color == c)
        self._syncing = False

    def _wheel_changed(self, hue: float, sat: float) -> None:
        if self._syncing:
            return
        v = max(self.value_slider.value(), 1) / 100
        q = QColor.fromHsvF(hue, sat, v)
        self._set_edited_color((q.red(), q.green(), q.blue()))

    def _value_changed(self, value: int) -> None:
        if self._syncing:
            return
        h, s, _ = colorsys.rgb_to_hsv(*(x / 255 for x in self._edited_color()))
        r, g, b = colorsys.hsv_to_rgb(h, s, value / 100)
        self._set_edited_color((round(r * 255), round(g * 255), round(b * 255)))

    def _hex_changed(self) -> None:
        try:
            self._set_edited_color(parse_color(self.hex_edit.text()))
        except ValueError:
            self._show_color(self._edited_color())

    def _spins_changed(self) -> None:
        if not self._syncing:
            r, g, b = (s.value() for s in self.rgb_spins)
            self._set_edited_color((r, g, b))

    def _swatch_picked(self, c: tuple[int, int, int]) -> None:
        self._set_edited_color(c)

    def _zone_changed(self) -> None:
        self._show_color(self._edited_color())

    def _reset_zones(self) -> None:
        self.settings.zone_colors.clear()
        self._show_color(self._edited_color())
        self._push()

    # -------------------------------------------------------------- effect

    def _select_effect(self, effect: Effect) -> None:
        self.effect_box.setCurrentIndex(self.effect_box.findData(effect))

    def _changed(self) -> None:
        if self._syncing:
            return
        s = self.settings
        s.effect = Effect(self.effect_box.currentData())
        s.brightness = self.brightness.value()
        s.speed = self.speed.value()
        s.source = str(self.source.currentData())
        s.temp_stops = self._steps()
        s.temp_blend = self.blend.isChecked()
        s.alert_temp = float(self.alert_temp.value()) if self.alert_on.isChecked() else 0.0
        s.alert_color = self.alert_color.color
        self._update_enabled()
        self._push()

    def _update_enabled(self) -> None:
        self.alert_temp.setEnabled(self.alert_on.isChecked())
        self.alert_color.setEnabled(self.alert_on.isChecked())
        self.speed.setEnabled(self.settings.animated)

    def _sync_from_settings(self) -> None:
        s = self.settings
        self._syncing = True
        self._select_effect(s.effect)
        self.brightness.setValue(s.brightness)
        self.speed.setValue(s.speed)
        self.source.setCurrentIndex(max(self.source.findData(s.source), 0))
        self._set_steps(s.temp_stops)
        self.blend.setChecked(s.temp_blend)
        self.alert_on.setChecked(s.alert_temp > 0)
        self.alert_temp.setValue(s.alert_temp or 90)
        self.alert_color.set_color(s.alert_color)
        self._syncing = False
        self._update_enabled()
        self._show_color(self._edited_color())
        self._push()

    def _push(self) -> None:
        self.output.set_settings(copy.deepcopy(self.settings))
        self._preview.setInterval(33 if self.settings.animated else 200)

    # ------------------------------------------------------------- preview

    def _sensor_loop(self) -> None:
        assert self._provider is not None
        while not self._stop_sensors.is_set():
            try:
                values = pick_values(self._provider.read(), {})
            except Exception as e:  # sensor backends can throw .NET exceptions
                log.warning("sensor read failed: %s", e)
            else:
                self._values = values  # atomic swap; read by the UI timer
                self.output.update(values, time.monotonic())
            self._stop_sensors.wait(1.0)

    def _set_status(self, text: str, ok: bool) -> None:
        if self.status.text() != text:
            self.status.setText(("●  " if ok else "▲  ") + text)
            self.status.setProperty("state", "ok" if ok else "warn")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def _tick_preview(self) -> None:
        s = self.settings
        values = self._values_source() if self._values_source else self._values
        temp = values.get(s.source)
        if self._provider is None and self._values_source is None:
            self.sensor_label.setText("Sensors unavailable")
        else:
            self.sensor_label.setText(
                f"{temp:.0f} °C" if temp is not None else "Needs administrator"
            )
        self.gradient.set_data(s.temp_stops, s.temp_blend, temp)
        zones = self._zones or [("LEDs", 30)]
        self.strip.set_leds(compute_leds(s, zones, values, time.monotonic()))
        if self.output.error:
            self._set_status(self.output.error.split(" (")[0], ok=False)
        elif self.output.controller:
            self._set_status("Live", ok=True)

    # ------------------------------------------------------------ profiles

    def _refresh_profiles(self) -> None:
        self.profile_box.clear()
        self.profile_box.addItems(profiles.list_profiles())

    def _load_profile(self) -> None:
        name = self.profile_box.currentText()
        if not name:
            return
        try:
            loaded = profiles.load_profile(name)
        except ConfigError as e:
            QMessageBox.warning(self, "Profile", str(e))
            return
        loaded.enabled = True
        loaded.device = loaded.device or self.settings.device
        self.settings = loaded
        self._sync_from_settings()

    def _save_profile(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save profile", "Profile name:", text=self.profile_box.currentText()
        )
        if not ok or not name.strip():
            return
        try:
            profiles.save_profile(name, self.settings)
        except ConfigError as e:
            QMessageBox.warning(self, "Profile", str(e))
            return
        self._refresh_profiles()
        self.profile_box.setCurrentText(name.strip())

    def _delete_profile(self) -> None:
        name = self.profile_box.currentText()
        if name and QMessageBox.question(self, "Delete", f"Delete profile '{name}'?") == (
            QMessageBox.StandardButton.Yes
        ):
            profiles.delete_profile(name)
            self._refresh_profiles()

    def _save_default(self) -> None:
        cfg = config_mod.load()
        cfg.rgb = copy.deepcopy(self.settings)
        path = config_mod.save(cfg)
        self._saved_rgb = copy.deepcopy(self.settings)
        QMessageBox.information(self, "Saved", f"Startup lighting saved to\n{path}")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._preview.stop()
        self._stop_sensors.set()
        if self._shared:
            self.output.set_settings(copy.deepcopy(self._saved_rgb))  # drop unsaved edits
        else:
            self.output.close()
        if self._provider is not None:
            self._provider.close()
        event.accept()


def main(fake_sensors: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    provider: SensorProvider | None
    try:
        if fake_sensors:
            from caseview.sensors.fake import FakeProvider

            provider = FakeProvider()
        else:
            from caseview.sensors.lhm import LhmProvider

            provider = LhmProvider()
    except Exception as e:
        log.warning("no sensors for the lighting window: %s", e)
        provider = None
    window = RgbWindow(provider)
    window.show()
    return int(app.exec())
