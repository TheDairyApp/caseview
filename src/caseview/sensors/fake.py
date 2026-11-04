"""Deterministic provider for tests and demos (`caseview run --fake`)."""

from __future__ import annotations

import math
import time

from caseview.sensors.base import Reading


class FakeProvider:
    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def read(self) -> list[Reading]:
        t = time.monotonic() - self._t0
        wave = (math.sin(t / 5) + 1) / 2  # 0..1, period ~31 s
        return [
            Reading("Cpu", "Fake CPU", "Temperature", "CPU Package", 35 + 50 * wave),
            Reading("SuperIO", "Fake Board", "Fan", "CPU Fan", 800 + 2200 * wave),
            Reading("GpuNvidia", "Fake GPU", "Temperature", "GPU Core", 30 + 45 * wave),
        ]

    def close(self) -> None:
        pass
