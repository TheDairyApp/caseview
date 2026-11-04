# Crystal X V331 case display: protocol notes

These notes come from our own interoperability testing. They are not vendor documentation.

## Device

| | |
|---|---|
| USB ID | `5131:2007` |
| Class | HID, usage page `0x000C` (Consumer), usage `0x01` |
| Report lengths | input / output / feature: 65 bytes (1 report-ID byte + 64) |
| Driver | Windows' built-in `input.inf`; no vendor driver needed |

## Update report

One 65-byte output report, written about once per second:

| Byte | Meaning |
|---|---|
| 0 | Report ID `0x00` |
| 1 | Command `0x40` (update) |
| 2 | "CPU" readout, 2 digits (0–99) |
| 3–5 | Unused by this panel (0) |
| 6–7 | "FAN" readout, 4 digits, **big-endian** (0–9999) |
| 8–9 | Unused (0) |
| 10 | "GPU" readout, 2 digits (0–99) |
| 11–64 | Unused (0) |

- No checksum and no reply. The panel holds the last values until new ones arrive.
- Values above 99 on the 2-digit readouts don't display correctly, so CaseView caps them at 99.

## Firmware quirk: fan readout tens digit

The panel draws the 4-digit readout's tens digit as

```
tens = ((value // 10) & 0xFF) % 10        # instead of (value // 10) % 10
```

So values **≥ 2560** show a wrong tens digit, e.g. 3456 shows as 3496. Every other digit is correct, and values below 2560 are always correct.

CaseView works around it: for the wanted number it picks the value to send whose buggy rendering shows the wanted digits (`fan_wire_value` in `src/caseview/drivers/crystalx_v331.py`). A handful of values next to a multiple of 2560 can't be shown exactly and are off by at most one tens step. The model is checked against 22 values observed on real hardware (`tests/test_crystalx_v331.py`).

## Coexistence

The vendor app (process `LCD-CS.exe`) writes the same report every second. Two writers make the panel flicker between their values, so CaseView pauses while that process runs, or closes it if `on_conflict = "stop"`.
