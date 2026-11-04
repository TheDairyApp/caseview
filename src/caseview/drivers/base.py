"""Driver interface shared by all display drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from caseview.transport.hid import HidLink


class SlotKind(StrEnum):
    TEMPERATURE = "temperature"
    RPM = "rpm"
    PERCENT = "percent"
    NUMBER = "number"


@dataclass(frozen=True)
class SlotSpec:
    """One readout on the display. `max_value` is what the hardware can actually show."""

    id: str
    label: str
    kind: SlotKind
    digits: int
    max_value: int

    def clamp(self, value: float) -> int:
        return max(0, min(self.max_value, round(value)))


class DisplayDriver(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    match: ClassVar[list[tuple[int, int]]]
    slots: ClassVar[list[SlotSpec]]
    update_interval_s: ClassVar[float] = 1.0

    def __init__(self, link: HidLink) -> None:
        self._link = link

    @abstractmethod
    def encode(self, values: dict[str, float]) -> bytes:
        """Build the report for these slot values. Missing slots show 0."""

    def render(self, values: dict[str, float]) -> None:
        self._link.write(self.encode(values))

    def close(self) -> None:
        self._link.close()
