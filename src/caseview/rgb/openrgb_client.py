"""Minimal client for the OpenRGB SDK network protocol (TCP, default port 6742).

Written from the public protocol description. We speak protocol version 0,
which every OpenRGB server supports, and only use what CaseView needs:
list controllers, switch one to its custom/direct mode, and set LED colors.

Packet header (little-endian): b"ORGB", u32 device index, u32 packet id, u32 payload size.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

MAGIC = b"ORGB"
HEADER = struct.Struct("<4sIII")

REQUEST_CONTROLLER_COUNT = 0
REQUEST_CONTROLLER_DATA = 1
SET_CLIENT_NAME = 50
RGBCONTROLLER_UPDATELEDS = 1050
RGBCONTROLLER_SETCUSTOMMODE = 1100

Color = tuple[int, int, int]


class OpenRgbError(ConnectionError):
    pass


@dataclass(frozen=True)
class Zone:
    name: str
    led_count: int


@dataclass(frozen=True)
class Controller:
    index: int
    name: str
    modes: list[str]
    zones: list[Zone]
    led_count: int


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def take(self, fmt: str) -> int:
        (value,) = struct.unpack_from("<" + fmt, self._data, self._pos)
        self._pos += struct.calcsize(fmt)
        return int(value)

    def skip(self, n: int) -> None:
        self._pos += n

    def string(self) -> str:
        n = self.take("H")
        raw = self._data[self._pos : self._pos + n]
        self._pos += n
        return raw.rstrip(b"\0").decode("utf-8", "replace")


def parse_controller(index: int, data: bytes) -> Controller:
    """Parse a protocol-v0 controller description."""
    r = _Reader(data)
    r.take("I")  # data size
    r.take("i")  # device type
    name = r.string()
    for _ in range(4):  # description, version, serial, location
        r.string()
    modes = []
    num_modes = r.take("H")
    r.take("i")  # active mode
    for _ in range(num_modes):
        modes.append(r.string())
        r.skip(4 * 9)  # value, flags, speed min/max, colors min/max, speed, direction, color mode
        r.skip(4 * r.take("H"))  # mode colors
    zones = []
    for _ in range(r.take("H")):
        zone_name = r.string()
        r.take("i")  # zone type
        r.take("I")  # leds min
        r.take("I")  # leds max
        count = r.take("I")
        if r.take("H"):  # matrix map length
            height, width = r.take("I"), r.take("I")
            r.skip(4 * height * width)
        zones.append(Zone(zone_name, count))
    num_leds = r.take("H")
    return Controller(index, name, modes, zones, num_leds)


class OpenRgbClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6742,
        name: str = "CaseView",
        timeout_s: float = 2.0,
    ) -> None:
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout_s)
        except OSError as e:
            raise OpenRgbError(
                f"OpenRGB SDK server not reachable on {host}:{port} "
                "(start OpenRGB and enable its SDK server)"
            ) from e
        self._send(0, SET_CLIENT_NAME, name.encode() + b"\0")

    def _send(self, device: int, packet_id: int, payload: bytes = b"") -> None:
        try:
            self._sock.sendall(HEADER.pack(MAGIC, device, packet_id, len(payload)) + payload)
        except OSError as e:
            raise OpenRgbError(f"OpenRGB connection lost: {e}") from e

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except OSError as e:
                raise OpenRgbError(f"OpenRGB connection lost: {e}") from e
            if not chunk:
                raise OpenRgbError("OpenRGB closed the connection")
            buf += chunk
        return bytes(buf)

    def _request(self, device: int, packet_id: int) -> bytes:
        self._send(device, packet_id)
        magic, _, reply_id, size = HEADER.unpack(self._recv_exact(HEADER.size))
        if magic != MAGIC or reply_id != packet_id:
            raise OpenRgbError(f"unexpected reply {magic!r} id={reply_id}")
        return self._recv_exact(size)

    def controllers(self) -> list[Controller]:
        (count,) = struct.unpack("<I", self._request(0, REQUEST_CONTROLLER_COUNT))
        return [
            parse_controller(i, self._request(i, REQUEST_CONTROLLER_DATA)) for i in range(count)
        ]

    def set_custom_mode(self, controller: Controller) -> None:
        self._send(controller.index, RGBCONTROLLER_SETCUSTOMMODE)

    def set_color(self, controller: Controller, color: Color) -> None:
        self.set_leds(controller, [color] * controller.led_count)

    def set_leds(self, controller: Controller, colors: list[Color]) -> None:
        """Set every LED; `colors` is in controller order (zones concatenated)."""
        if len(colors) != controller.led_count:
            raise ValueError(f"expected {controller.led_count} colors, got {len(colors)}")
        payload = struct.pack("<H", len(colors)) + b"".join(bytes((*c, 0)) for c in colors)
        self._send(
            controller.index,
            RGBCONTROLLER_UPDATELEDS,
            struct.pack("<I", 4 + len(payload)) + payload,
        )

    def close(self) -> None:
        self._sock.close()
