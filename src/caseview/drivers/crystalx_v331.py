"""Crystal X V331 case display (USB HID 5131:2007).

Protocol (verified on hardware): one 65-byte output report per second,
report ID 0x00, then 0x40 and fixed slots:

    byte 2      CPU temperature   (display shows 2 digits)
    bytes 6-7   CPU fan RPM, big-endian (4 digits)
    byte 10     GPU temperature   (2 digits)

Other bytes carry load/clock/power fields that this panel does not show;
we leave them zero.

Firmware quirk: the fan's tens digit is drawn as ((rpm // 10) & 0xFF) % 10,
so values >= 2560 show a wrong tens digit. `fan_wire_value` picks the value
to send so the panel shows the intended number.
"""

from __future__ import annotations

from typing import ClassVar

from caseview.drivers.base import DisplayDriver, SlotKind, SlotSpec

REPORT_LEN = 65
CMD_UPDATE = 0x40


def shown_fan(sent: int) -> int:
    """What the panel displays for a fan value it receives."""
    tens = ((sent // 10) & 0xFF) % 10
    return sent // 100 * 100 + tens * 10 + sent % 10


def fan_wire_value(wanted: int) -> int:
    """Value to send so the panel shows `wanted` (or the closest it can)."""
    base = wanted // 100 * 100 + wanted % 10
    best = min(range(10), key=lambda t: abs(shown_fan(base + t * 10) - wanted))
    return base + best * 10


class CrystalXV331(DisplayDriver):
    id = "crystalx-v331"
    name = "Crystal X V331 case display"
    match: ClassVar[list[tuple[int, int]]] = [(0x5131, 0x2007)]
    slots: ClassVar[list[SlotSpec]] = [
        SlotSpec("cpu_temp", "CPU temperature", SlotKind.TEMPERATURE, 2, 99),
        SlotSpec("cpu_fan", "CPU fan", SlotKind.RPM, 4, 9999),
        SlotSpec("gpu_temp", "GPU temperature", SlotKind.TEMPERATURE, 2, 99),
    ]

    def encode(self, values: dict[str, float]) -> bytes:
        cpu_temp, cpu_fan, gpu_temp = (s.clamp(values.get(s.id, 0)) for s in self.slots)
        fan = fan_wire_value(cpu_fan)
        report = bytearray(REPORT_LEN)
        report[1] = CMD_UPDATE
        report[2] = cpu_temp
        report[6:8] = fan.to_bytes(2, "big")
        report[10] = gpu_temp
        return bytes(report)
