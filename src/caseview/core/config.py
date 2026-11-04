"""User settings, stored as TOML in %APPDATA%\\CaseView\\config.toml.

Example:

    [display]
    on_conflict = "wait"        # DT Control running: "wait" for it to exit, or "stop" it

    [slots.cpu_temp]
    sensor = "auto"             # or a sensor name ("Core Max") or full key from `caseview sensors`

    [slots.cpu_fan]
    sensor = "Fan #1"

    [rgb]                       # needs OpenRGB running with its SDK server on
    enabled = true
    device = "B650"             # part of the OpenRGB device name; "" = first device
    effect = "temperature"      # static | temperature | breathing | color_cycle | rainbow | off
    brightness = 100            # 0-100 %
    speed = 5                   # 1-10, animated effects
    source = "cpu_temp"
    temp_blend = true           # blend between steps; false = one solid color per band
    temp_stops = [              # [temperature °C, color] — below 50 blue ... 90+ red
        [50, "#0050FF"], [60, "#00FF50"], [70, "#FFC800"], [80, "#FF5A00"], [90, "#FF0000"],
    ]
    alert_temp = 90             # flash alert_color at/above this; 0 = off

    [rgb.zone_colors]           # static effect: per-zone colors (zone names from OpenRGB)
    D_LED = "#00FFAA"
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomli_w

from caseview.rgb.effects import Effect, RgbSettings, format_color, parse_color

AUTO = "auto"
_RGB_COLORS = ("color", "alert_color")
_RGB_NUMBERS = ("alert_temp",)
_RGB_PERCENT = {"brightness": (0, 100), "speed": (1, 10)}


class OnConflict(StrEnum):
    WAIT = "wait"
    STOP = "stop"


class ConfigError(ValueError):
    pass


@dataclass
class SlotConfig:
    sensor: str = AUTO


SLOT_IDS = ("cpu_temp", "cpu_fan", "gpu_temp")  # the Crystal X's three readouts


@dataclass
class DisplayPage:
    """One screen of the case display: what each of the three readouts shows.

    Readouts are named after the labels printed on the case: `cpu_temp` (2 digits),
    `cpu_fan` (4 digits) and `gpu_temp` (2 digits). Values are keys from
    caseview.core.metrics.DISPLAY_SOURCES.
    """

    name: str
    cpu_temp: str
    cpu_fan: str
    gpu_temp: str
    enabled: bool = True

    def sources(self) -> dict[str, str]:
        return {"cpu_temp": self.cpu_temp, "cpu_fan": self.cpu_fan, "gpu_temp": self.gpu_temp}


def default_pages() -> list[DisplayPage]:
    return [
        DisplayPage("Power", cpu_temp="cpu_amps", cpu_fan="total_power", gpu_temp="gpu_amps"),
        DisplayPage("CPU", cpu_temp="ram_pct", cpu_fan="cpu_clock", gpu_temp="disk_pct"),
        DisplayPage("GPU", cpu_temp="gpu_load", cpu_fan="gpu_clock", gpu_temp="gpu_vram_pct"),
        DisplayPage("Temp", cpu_temp="cpu_temp", cpu_fan="cpu_fan", gpu_temp="gpu_temp"),
    ]


@dataclass
class PowerSettings:
    mains_voltage: float = 230.0  # wall socket voltage (230 Europe/Asia, 120 North America)
    psu_efficiency: float = 0.90  # PSU efficiency at typical load (80 PLUS Gold ~0.90)
    other_watts: float = 50.0  # motherboard, RAM, drives, fans, RGB (not measured)


@dataclass
class UiSettings:
    stats_bar: bool = True
    stats_bar_pos: tuple[int, int] | None = None  # None = above the taskbar, near the clock
    web_enabled: bool = False  # phone access on the local network
    web_port: int = 8765
    web_pin: str = ""  # generated on first use


@dataclass
class Config:
    on_conflict: OnConflict = OnConflict.WAIT
    reconnect_interval_s: float = 3.0
    sensor_interval_s: float = 2.0  # hardware sensor refresh; lower = more CPU
    slots: dict[str, SlotConfig] = field(default_factory=dict)
    rgb: RgbSettings = field(default_factory=RgbSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    power: PowerSettings = field(default_factory=PowerSettings)
    pages: list[DisplayPage] = field(default_factory=default_pages)
    page_seconds: float = 5.0  # how long each display page stays before the next one

    def enabled_pages(self) -> list[DisplayPage]:
        return [p for p in self.pages if p.enabled] or self.pages[:1]

    def sensor_for(self, slot_id: str) -> str:
        return self.slots.get(slot_id, SlotConfig()).sensor

    def to_toml(self) -> str:
        data: dict[str, Any] = {
            "display": {
                "on_conflict": self.on_conflict.value,
                "reconnect_interval_s": self.reconnect_interval_s,
                "sensor_interval_s": self.sensor_interval_s,
                "page_seconds": self.page_seconds,
            },
            "slots": {k: {"sensor": v.sensor} for k, v in self.slots.items()},
            "pages": [{"name": p.name, "enabled": p.enabled, **p.sources()} for p in self.pages],
            "rgb": rgb_to_dict(self.rgb),
            "power": {
                "mains_voltage": self.power.mains_voltage,
                "psu_efficiency": self.power.psu_efficiency,
                "other_watts": self.power.other_watts,
            },
            "ui": {
                "stats_bar": self.ui.stats_bar,
                **({"stats_bar_pos": list(self.ui.stats_bar_pos)} if self.ui.stats_bar_pos else {}),
                "web_enabled": self.ui.web_enabled,
                "web_port": self.ui.web_port,
                "web_pin": self.ui.web_pin,
            },
        }
        return tomli_w.dumps(data)

    @classmethod
    def from_toml(cls, text: str) -> Config:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML: {e}") from e
        display = data.get("display", {})
        try:
            on_conflict = OnConflict(display.get("on_conflict", OnConflict.WAIT))
        except ValueError as e:
            raise ConfigError("display.on_conflict must be 'wait' or 'stop'") from e
        interval = display.get("reconnect_interval_s", 3.0)
        if not isinstance(interval, int | float) or interval <= 0:
            raise ConfigError("display.reconnect_interval_s must be a positive number")
        sensor_interval = display.get("sensor_interval_s", 2.0)
        if not isinstance(sensor_interval, int | float) or not 0.5 <= sensor_interval <= 30:
            raise ConfigError("display.sensor_interval_s must be between 0.5 and 30 seconds")
        slots: dict[str, SlotConfig] = {}
        for slot_id, slot in data.get("slots", {}).items():
            sensor = slot.get("sensor", AUTO) if isinstance(slot, dict) else None
            if not isinstance(sensor, str) or not sensor:
                raise ConfigError(f"slots.{slot_id}.sensor must be a non-empty string")
            slots[slot_id] = SlotConfig(sensor)
        page_seconds = display.get("page_seconds", 5.0)
        if not isinstance(page_seconds, int | float) or not 1 <= page_seconds <= 600:
            raise ConfigError("display.page_seconds must be between 1 and 600")
        return cls(
            on_conflict,
            float(interval),
            float(sensor_interval),
            slots,
            rgb_from_dict(data.get("rgb", {})),
            _parse_ui(data.get("ui", {})),
            _parse_power(data.get("power", {})),
            _parse_pages(data.get("pages"), slots),
            float(page_seconds),
        )


def _parse_pages(raw: Any, slots: dict[str, SlotConfig]) -> list[DisplayPage]:
    if raw is None:
        # Settings from before pages: readouts set to a metric become one "Custom" page.
        from caseview.core.metrics import DISPLAY_SOURCES

        custom = {k: v.sensor for k, v in slots.items() if v.sensor in DISPLAY_SOURCES}
        if custom:
            return [
                DisplayPage(
                    "Custom",
                    cpu_temp=custom.get("cpu_temp", "cpu_temp"),
                    cpu_fan=custom.get("cpu_fan", "cpu_fan"),
                    gpu_temp=custom.get("gpu_temp", "gpu_temp"),
                )
            ]
        return default_pages()
    if not isinstance(raw, list) or not raw:
        raise ConfigError("pages must be a non-empty list of [[pages]] tables")
    pages = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"pages[{i}] must be a table")
        try:
            pages.append(
                DisplayPage(
                    name=str(item.get("name", f"Page {i + 1}"))[:24],
                    enabled=bool(item.get("enabled", True)),
                    **{s: str(item.get(s, s)) for s in SLOT_IDS},
                )
            )
        except TypeError as e:
            raise ConfigError(f"pages[{i}]: {e}") from e
    return pages


def _parse_power(raw: dict[str, Any]) -> PowerSettings:
    p = PowerSettings()
    limits = {"mains_voltage": (90, 260), "psu_efficiency": (0.5, 1.0), "other_watts": (0, 500)}
    for key, (lo, hi) in limits.items():
        value = raw.get(key, getattr(p, key))
        if not isinstance(value, int | float) or not lo <= value <= hi:
            raise ConfigError(f"power.{key} must be a number from {lo} to {hi}")
        setattr(p, key, float(value))
    return p


def _parse_ui(raw: dict[str, Any]) -> UiSettings:
    u = UiSettings()
    u.stats_bar = bool(raw.get("stats_bar", u.stats_bar))
    pos = raw.get("stats_bar_pos")
    if isinstance(pos, list) and len(pos) == 2 and all(isinstance(n, int) for n in pos):
        u.stats_bar_pos = (pos[0], pos[1])
    u.web_enabled = bool(raw.get("web_enabled", u.web_enabled))
    port = raw.get("web_port", u.web_port)
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ConfigError("ui.web_port must be a number from 1024 to 65535")
    u.web_port = port
    pin = str(raw.get("web_pin", ""))
    u.web_pin = pin if pin.isdigit() and len(pin) >= 6 else ""
    return u


def rgb_to_dict(rgb: RgbSettings) -> dict[str, Any]:
    return {
        "enabled": rgb.enabled,
        "device": rgb.device,
        "effect": rgb.effect.value,
        "source": rgb.source,
        **{k: getattr(rgb, k) for k in _RGB_NUMBERS},
        **{k: getattr(rgb, k) for k in _RGB_PERCENT},
        **{k: format_color(getattr(rgb, k)) for k in _RGB_COLORS},
        "zone_colors": {z: format_color(c) for z, c in rgb.zone_colors.items()},
        "temp_blend": rgb.temp_blend,
        "temp_stops": [[t, format_color(c)] for t, c in sorted(rgb.temp_stops)],
    }


def rgb_from_dict(raw: dict[str, Any]) -> RgbSettings:
    s = RgbSettings()
    try:
        s.enabled = bool(raw.get("enabled", s.enabled))
        s.device = str(raw.get("device", s.device))
        s.effect = Effect(raw.get("effect", s.effect))
        s.source = str(raw.get("source", s.source))
        for k in _RGB_NUMBERS:
            value = raw.get(k, getattr(s, k))
            if not isinstance(value, int | float):
                raise ValueError(f"{k} must be a number, got {value!r}")
            setattr(s, k, float(value))
        for k, (lo, hi) in _RGB_PERCENT.items():
            value = raw.get(k, getattr(s, k))
            if not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{k} must be a whole number {lo}-{hi}, got {value!r}")
            setattr(s, k, value)
        for k in _RGB_COLORS:
            if k in raw:
                setattr(s, k, parse_color(str(raw[k])))
        zones = raw.get("zone_colors", {})
        if not isinstance(zones, dict):
            raise ValueError('zone_colors must be a table of zone = "#RRGGBB"')
        s.zone_colors = {str(z): parse_color(str(c)) for z, c in zones.items()}
        s.temp_blend = bool(raw.get("temp_blend", s.temp_blend))
        if "temp_stops" in raw:
            s.temp_stops = _parse_stops(raw["temp_stops"])
        elif "cold_temp" in raw or "hot_temp" in raw:  # settings from before temp steps
            cold = parse_color(str(raw.get("cold_color", "#0050FF")))
            hot = parse_color(str(raw.get("hot_color", "#FF0000")))
            s.temp_stops = [
                (float(raw.get("cold_temp", 35)), cold),
                (float(raw.get("hot_temp", 80)), hot),
            ]
    except ValueError as e:
        raise ConfigError(f"[rgb]: {e}") from e
    return s


def _parse_stops(raw: Any) -> list[tuple[float, tuple[int, int, int]]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError('temp_stops must be a list like [[50, "#0050FF"], [80, "#FF0000"]]')
    stops = []
    for item in raw:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], int | float)):
            raise ValueError(
                f'each temp_stops entry must be [temperature, "#RRGGBB"], got {item!r}'
            )
        stops.append((float(item[0]), parse_color(str(item[1]))))
    return sorted(stops)


def default_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "CaseView" / "config.toml"


def load(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    return Config.from_toml(path.read_text(encoding="utf-8"))


def save(config: Config, path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(config.to_toml(), encoding="utf-8")
    tmp.replace(path)
    return path
