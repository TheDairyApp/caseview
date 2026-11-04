# Adding support for a new case display

CaseView talks to each display model through a small **driver** class. A driver declares the display's readouts and turns slot values into bytes.

## 1. Identify the device

```powershell
caseview list-devices
```

Unplug the display's USB cable (or disable it in Device Manager), run the command again, and see which entry disappears. Note its `VID:PID` and HID usage page.

## 2. Capture what the vendor app sends

1. Install [USBPcap](https://desowin.org/usbpcap/) and [Wireshark](https://www.wireshark.org/).
2. Start a capture on the USB root hub the display hangs off, with the vendor app running.
3. Filter: `usb.idVendor == 0x1234 && usb.idProduct == 0x5678`, or by device address.
4. Look at the `URB_INTERRUPT out` packets: change one value at a time (warm the CPU, change fan speed) and see which bytes move.

Please attach a short capture (`.pcapng`) to your *New display support* issue, together with:
- a photo of the display showing its readouts
- `caseview list-devices` output
- what each readout normally shows

**Never send guessed commands to a device you can't recover.** Only send byte patterns you've seen the vendor app send.

## 3. Write the driver

```python
from typing import ClassVar

from caseview.drivers.base import DisplayDriver, SlotKind, SlotSpec


class MyDisplay(DisplayDriver):
    id = "vendor-model"
    name = "Vendor Model case display"
    match: ClassVar[list[tuple[int, int]]] = [(0x1234, 0x5678)]
    slots: ClassVar[list[SlotSpec]] = [
        SlotSpec("cpu_temp", "CPU temperature", SlotKind.TEMPERATURE, digits=2, max_value=99),
        SlotSpec("gpu_temp", "GPU temperature", SlotKind.TEMPERATURE, digits=2, max_value=99),
    ]

    def encode(self, values: dict[str, float]) -> bytes:
        cpu, gpu = (s.clamp(values.get(s.id, 0)) for s in self.slots)
        report = bytearray(65)
        report[1] = 0x40
        report[2], report[3] = cpu, gpu
        return bytes(report)
```

- Put built-in drivers in `src/caseview/drivers/` and add them to `BUILTIN` in `drivers/__init__.py`.
- Third-party packages can register a driver without changing CaseView:

  ```toml
  [project.entry-points."caseview.drivers"]
  vendor-model = "my_package.driver:MyDisplay"
  ```

## 4. Test it

- Add `tests/test_<driver>.py` covering `encode()` byte layout, clamping and any firmware quirks. See `tests/test_crystalx_v331.py`.
- `caseview test-pattern` and `caseview send --cpu-temp 12 --cpu-fan 3456 --gpu-temp 78` let you check the real panel.
