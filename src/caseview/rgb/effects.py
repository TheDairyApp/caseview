"""Pure color effects: (settings, zones, sensor values, time) -> one color per LED."""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum

Color = tuple[int, int, int]


class Effect(StrEnum):
    STATIC = "static"
    TEMPERATURE = "temperature"
    BREATHING = "breathing"
    COLOR_CYCLE = "color_cycle"
    RAINBOW = "rainbow"
    OFF = "off"


ANIMATED = {Effect.BREATHING, Effect.COLOR_CYCLE, Effect.RAINBOW}

TempStop = tuple[float, Color]  # (temperature in °C, color at that temperature)

DEFAULT_TEMP_STOPS: list[TempStop] = [
    (40.0, (0, 80, 255)),  # blue
    (50.0, (0, 170, 255)),  # light blue
    (60.0, (0, 255, 80)),  # green
    (70.0, (255, 200, 0)),  # yellow
    (80.0, (255, 90, 0)),  # orange
    (90.0, (255, 0, 0)),  # red
]


def parse_color(text: str) -> Color:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
    if not m:
        raise ValueError(f"color must look like #RRGGBB, got {text!r}")
    v = int(m.group(1), 16)
    return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF


def format_color(c: Color) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


def lerp(a: Color, b: Color, t: float) -> Color:
    t = max(0.0, min(1.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def scale(c: Color, k: float) -> Color:
    k = max(0.0, min(1.0, k))
    return round(c[0] * k), round(c[1] * k), round(c[2] * k)


def hue_color(h: float) -> Color:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return round(r * 255), round(g * 255), round(b * 255)


@dataclass
class RgbSettings:
    enabled: bool = False
    device: str = ""  # substring of the OpenRGB device name; "" = first device
    effect: Effect = Effect.TEMPERATURE
    brightness: int = 100  # percent
    speed: int = 5  # 1 (slow) .. 10 (fast), animated effects
    color: Color = (0, 120, 255)  # static / breathing base color
    zone_colors: dict[str, Color] = field(default_factory=dict)  # static per-zone overrides
    source: str = "cpu_temp"  # display slot that drives temperature + alert
    temp_stops: list[TempStop] = field(default_factory=lambda: list(DEFAULT_TEMP_STOPS))
    temp_blend: bool = True  # blend between steps; False = solid color per band
    alert_temp: float = 90.0  # 0 disables the alert flash
    alert_color: Color = (255, 0, 0)

    @property
    def animated(self) -> bool:
        return self.effect in ANIMATED


def temperature_color(stops: list[TempStop], temp: float | None, blend: bool = True) -> Color:
    """Color for `temp` from steps like [(50, blue), (60, green), (80, red)].

    Below the first step uses the first color, above the last uses the last.
    Between two steps the color blends (or, with blend=False, holds the lower step's color).
    """
    if not stops:
        return (0, 0, 0)
    ordered = sorted(stops)
    if temp is None or temp <= ordered[0][0]:
        return ordered[0][1]
    for (t0, c0), (t1, c1) in zip(ordered, ordered[1:], strict=False):
        if temp < t1:
            return lerp(c0, c1, (temp - t0) / (t1 - t0)) if blend and t1 > t0 else c0
    return ordered[-1][1]


def _base_color(s: RgbSettings, temp: float | None) -> Color:
    if s.effect is Effect.TEMPERATURE:
        return temperature_color(s.temp_stops, temp, s.temp_blend)
    return s.color


def compute_leds(
    s: RgbSettings, zones: list[tuple[str, int]], values: dict[str, float], t: float
) -> list[Color]:
    """Colors for every LED, zones in controller order."""
    total = sum(n for _, n in zones)
    temp = values.get(s.source)
    k = s.brightness / 100
    rate = 0.05 * s.speed  # cycles per second at speed 1 = 0.05, speed 10 = 0.5

    if s.alert_temp > 0 and temp is not None and temp >= s.alert_temp:
        flash = s.alert_color if int(t * 2) % 2 == 0 else (0, 0, 0)
        return [scale(flash, k)] * total
    if s.effect is Effect.OFF:
        return [(0, 0, 0)] * total
    if s.effect is Effect.COLOR_CYCLE:
        return [scale(hue_color(t * rate), k)] * total
    if s.effect is Effect.RAINBOW:
        return [scale(hue_color(t * rate + i / max(total, 1)), k) for i in range(total)]
    if s.effect is Effect.BREATHING:
        level = (1 - math.cos(2 * math.pi * t * rate)) / 2
        return [scale(s.color, k * level)] * total

    leds: list[Color] = []
    base = _base_color(s, temp)
    for name, count in zones:
        zone_color = s.zone_colors.get(name, base) if s.effect is Effect.STATIC else base
        leds += [scale(zone_color, k)] * count
    return leds


def compute_color(s: RgbSettings, values: dict[str, float], t: float) -> Color:
    """Single representative color (first LED) for status display."""
    return compute_leds(s, [("all", 1)], values, t)[0]
