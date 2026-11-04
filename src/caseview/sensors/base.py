"""Provider interface and the default choice of which sensor feeds which display slot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Reading:
    hardware_type: str  # "Cpu", "GpuNvidia", "SuperIO", ...
    hardware: str  # "Intel Core i7-12700K"
    kind: str  # "Temperature", "Fan", "Load", ...
    name: str  # "CPU Package"
    value: float | None

    @property
    def key(self) -> str:
        return f"{self.hardware}/{self.kind}/{self.name}"


class SensorProvider(Protocol):
    def read(self) -> list[Reading]: ...
    def close(self) -> None: ...


def _first(readings: list[Reading], *preds: Callable[[Reading], bool]) -> float | None:
    for pred in preds:
        for r in readings:
            if r.value is not None and pred(r):
                return r.value
    return None


def find_sensor(readings: list[Reading], spec: str) -> float | None:
    """Match a configured sensor by full key ("hw/Kind/Name") or by name alone."""
    for r in readings:
        if r.value is not None and spec in (r.key, r.name):
            return r.value
    return None


def pick_values(readings: list[Reading], choices: dict[str, str]) -> dict[str, float]:
    """Slot values using configured sensors, falling back to the defaults for "auto"."""
    values = pick_default_values(readings)
    for slot_id, spec in choices.items():
        if spec == "auto":
            continue
        value = find_sensor(readings, spec)
        if value is None:
            values.pop(slot_id, None)
        else:
            values[slot_id] = value
    return values


def pick_default_values(readings: list[Reading]) -> dict[str, float]:
    """Pick CPU temp, CPU fan and GPU temp. Slots without a reading are left out."""
    cpu_t = [r for r in readings if r.hardware_type == "Cpu" and r.kind == "Temperature"]
    gpu_t = [r for r in readings if r.hardware_type.startswith("Gpu") and r.kind == "Temperature"]
    board_fans = [r for r in readings if r.hardware_type == "SuperIO" and r.kind == "Fan"]

    picks = {
        "cpu_temp": _first(
            cpu_t,
            lambda r: r.name in ("CPU Package", "Core (Tctl/Tdie)"),
            lambda r: r.name == "Core Max",
            lambda r: "Distance" not in r.name,
        ),
        "cpu_fan": _first(
            board_fans,
            lambda r: "cpu" in r.name.lower() and "opt" not in r.name.lower(),
            lambda r: r.name == "Fan #1",
            lambda r: (r.value or 0) > 0,
        ),
        "gpu_temp": _first(gpu_t, lambda r: r.name == "GPU Core", lambda r: True),
    }
    return {k: v for k, v in picks.items() if v is not None}
