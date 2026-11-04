"""USB HID transport.

`HidLink` is the small interface drivers write to. `HidapiLink` talks to real
hardware through hidapi; tests use a fake that records the reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HidDeviceInfo:
    vid: int
    pid: int
    path: bytes
    usage_page: int
    usage: int
    manufacturer: str
    product: str

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"


class HidLink(Protocol):
    def write(self, report: bytes) -> int: ...
    def close(self) -> None: ...


class HidError(RuntimeError):
    pass


def enumerate_devices(vid: int = 0, pid: int = 0) -> list[HidDeviceInfo]:
    import hid

    return [
        HidDeviceInfo(
            vid=d["vendor_id"],
            pid=d["product_id"],
            path=d["path"],
            usage_page=d.get("usage_page", 0),
            usage=d.get("usage", 0),
            manufacturer=d.get("manufacturer_string") or "",
            product=d.get("product_string") or "",
        )
        for d in hid.enumerate(vid, pid)
    ]


class HidapiLink:
    def __init__(self, path: bytes) -> None:
        import hid

        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except OSError as e:
            raise HidError(f"cannot open HID device {path!r}: {e}") from e

    def write(self, report: bytes) -> int:
        n: int = self._dev.write(report)
        if n < 0:
            raise HidError(f"HID write failed: {self._dev.error()}")
        return n

    def close(self) -> None:
        self._dev.close()
