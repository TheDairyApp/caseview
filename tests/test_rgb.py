import socket
import struct
import threading
from pathlib import Path

import pytest

from caseview.core.config import Config, ConfigError
from caseview.rgb import profiles
from caseview.rgb.effects import Effect, RgbSettings, compute_color, compute_leds, parse_color
from caseview.rgb.openrgb_client import (
    HEADER,
    MAGIC,
    RGBCONTROLLER_SETCUSTOMMODE,
    RGBCONTROLLER_UPDATELEDS,
    SET_CLIENT_NAME,
    OpenRgbClient,
    OpenRgbError,
)
from caseview.rgb.output import RgbOutput


def s(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("<H", len(raw)) + raw


def controller_blob(name: str, zones: list[tuple[str, int]]) -> bytes:
    body = struct.pack("<i", 0) + s(name) + s("desc") + s("1.0") + s("") + s("loc")
    body += struct.pack("<Hi", 2, 0)
    for mode in ("Direct", "Static"):
        body += s(mode) + struct.pack("<9I", *range(9)) + struct.pack("<H", 1) + b"\1\2\3\0"
    body += struct.pack("<H", len(zones))
    for zone, count in zones:
        body += s(zone) + struct.pack("<iIII", 0, 0, 100, count) + struct.pack("<H", 0)
    total = sum(c for _, c in zones)
    body += struct.pack("<H", total) + b"".join(s(f"LED {i}") + b"\0\0\0\0" for i in range(total))
    body += struct.pack("<H", total) + b"\0\0\0\0" * total
    return struct.pack("<I", 4 + len(body)) + body


class FakeOpenRgbServer:
    """Speaks just enough of the SDK protocol to test the client."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.received: list[tuple[int, int, bytes]] = []
        zones = [("D_LED", 58), ("LED_C1", 1)]
        self.blobs = [controller_blob("EXAMPLE BOARD WIFI", zones)]
        threading.Thread(target=self._serve, daemon=True).start()

    def _recv(self, conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        try:
            while True:
                magic, dev, pid, size = HEADER.unpack(self._recv(conn, HEADER.size))
                payload = self._recv(conn, size) if size else b""
                self.received.append((dev, pid, payload))
                reply = None
                if pid == 0:
                    reply = struct.pack("<I", len(self.blobs))
                elif pid == 1:
                    reply = self.blobs[dev]
                if reply is not None:
                    conn.sendall(HEADER.pack(MAGIC, dev, pid, len(reply)) + reply)
        except (EOFError, OSError):
            conn.close()


def test_client_lists_controllers_and_sets_colors() -> None:
    server = FakeOpenRgbServer()
    client = OpenRgbClient(port=server.port, name="CaseView")
    [ctrl] = client.controllers()
    assert ctrl.name == "EXAMPLE BOARD WIFI"
    assert ctrl.modes == ["Direct", "Static"]
    assert [(z.name, z.led_count) for z in ctrl.zones] == [("D_LED", 58), ("LED_C1", 1)]
    assert ctrl.led_count == 59
    client.set_custom_mode(ctrl)
    client.set_color(ctrl, (255, 16, 0))
    client.close()
    threading.Event().wait(0.2)

    assert server.received[0] == (0, SET_CLIENT_NAME, b"CaseView\0")
    assert (0, RGBCONTROLLER_SETCUSTOMMODE, b"") in server.received
    _, _, leds = next(r for r in server.received if r[1] == RGBCONTROLLER_UPDATELEDS)
    size, count = struct.unpack_from("<IH", leds)
    assert (size, count) == (len(leds), 59)
    assert leds[6:] == b"\xff\x10\x00\x00" * 59


def test_unreachable_server_raises_friendly_error() -> None:
    with pytest.raises(OpenRgbError, match="SDK server"):
        OpenRgbClient(port=1, timeout_s=0.5)


def test_temperature_gradient_and_alert_flash() -> None:
    stops = [(30.0, (0, 80, 255)), (80.0, (255, 0, 0))]
    st = RgbSettings(enabled=True, temp_stops=stops, alert_temp=90)
    assert compute_color(st, {"cpu_temp": 20}, 0) == (0, 80, 255)
    assert compute_color(st, {"cpu_temp": 85}, 0) == (255, 0, 0)
    assert compute_color(st, {"cpu_temp": 55}, 0) == (128, 40, 128)
    assert compute_color(st, {"cpu_temp": 95}, 0.0) == st.alert_color
    assert compute_color(st, {"cpu_temp": 95}, 0.6) == (0, 0, 0)


def test_static_color_ignores_temperature() -> None:
    st = RgbSettings(effect=Effect.STATIC, color=(1, 2, 3), alert_temp=0)
    assert compute_color(st, {"cpu_temp": 99}, 0) == (1, 2, 3)


def test_parse_color() -> None:
    assert parse_color("#FF8000") == (255, 128, 0)
    assert parse_color("00ff00") == (0, 255, 0)
    with pytest.raises(ValueError):
        parse_color("red")


def test_rgb_config_round_trip_and_validation() -> None:
    cfg = Config.from_toml('[rgb]\nenabled = true\neffect = "static"\ncolor = "#123456"')
    assert cfg.rgb.enabled and cfg.rgb.effect is Effect.STATIC and cfg.rgb.color == (18, 52, 86)
    assert Config.from_toml(cfg.to_toml()).rgb == cfg.rgb
    with pytest.raises(ConfigError):
        Config.from_toml('[rgb]\ncolor = "blue"')
    with pytest.raises(ConfigError):
        Config.from_toml('[rgb]\neffect = "sparkle"')


def test_output_is_disabled_by_default_and_survives_missing_openrgb() -> None:
    assert RgbOutput(RgbSettings()).update({"cpu_temp": 50}, 0) is None

    def unreachable() -> OpenRgbClient:
        raise OpenRgbError("not running")

    out = RgbOutput(RgbSettings(enabled=True), connect=unreachable)
    assert out.update({"cpu_temp": 50}, 0) is None
    assert out.error == "not running"


def test_output_picks_device_and_only_resends_on_change() -> None:
    server = FakeOpenRgbServer()
    out = RgbOutput(
        RgbSettings(enabled=True, device="example board", effect=Effect.STATIC, color=(9, 9, 9)),
        connect=lambda: OpenRgbClient(port=server.port),
    )
    for t in (0.0, 1.0, 2.0):
        assert out.update({}, t) == (9, 9, 9)
    out.close()
    threading.Event().wait(0.2)
    assert sum(1 for r in server.received if r[1] == RGBCONTROLLER_UPDATELEDS) == 1


ZONES = [("D_LED", 58), ("LED_C1", 1), ("PCI-E Accent", 1)]


def test_per_zone_static_colors_and_brightness() -> None:
    st = RgbSettings(
        effect=Effect.STATIC,
        color=(0, 0, 200),
        zone_colors={"LED_C1": (100, 0, 0)},
        brightness=50,
        alert_temp=0,
    )
    leds = compute_leds(st, ZONES, {}, 0)
    assert len(leds) == 60
    assert leds[0] == (0, 0, 100) and leds[58] == (50, 0, 0) and leds[59] == (0, 0, 100)


def test_animated_effects_change_over_time() -> None:
    for effect in (Effect.BREATHING, Effect.COLOR_CYCLE, Effect.RAINBOW):
        st = RgbSettings(effect=effect, alert_temp=0)
        assert st.animated
        assert compute_leds(st, ZONES, {}, 0.0) != compute_leds(st, ZONES, {}, 1.3)
    rainbow = compute_leds(RgbSettings(effect=Effect.RAINBOW), ZONES, {}, 0)
    assert len(set(rainbow)) > 30  # different hue along the strip


def test_off_and_alert_override() -> None:
    assert set(compute_leds(RgbSettings(effect=Effect.OFF, alert_temp=0), ZONES, {}, 0)) == {
        (0, 0, 0)
    }
    st = RgbSettings(effect=Effect.RAINBOW, alert_temp=80)
    assert set(compute_leds(st, ZONES, {"cpu_temp": 85}, 0)) == {st.alert_color}


def test_new_fields_round_trip_and_validate() -> None:
    cfg = Config.from_toml(
        '[rgb]\neffect = "rainbow"\nbrightness = 40\nspeed = 8\n'
        '[rgb.zone_colors]\nD_LED = "#00FFAA"\n'
    )
    assert (cfg.rgb.brightness, cfg.rgb.speed, cfg.rgb.zone_colors) == (
        40,
        8,
        {"D_LED": (0, 255, 170)},
    )
    assert Config.from_toml(cfg.to_toml()).rgb == cfg.rgb
    for bad in ("brightness = 150", "speed = 0", 'zone_colors = "red"'):
        with pytest.raises(ConfigError):
            Config.from_toml(f"[rgb]\n{bad}\n")


def test_profiles_save_list_load_delete(tmp_path: Path) -> None:
    st = RgbSettings(enabled=True, effect=Effect.BREATHING, color=(1, 2, 3), speed=3)
    profiles.save_profile("Night Mode", st, tmp_path)
    assert profiles.list_profiles(tmp_path) == ["Night Mode"]
    assert profiles.load_profile("Night Mode", tmp_path) == st
    profiles.delete_profile("Night Mode", tmp_path)
    assert profiles.list_profiles(tmp_path) == []
    with pytest.raises(ConfigError):
        profiles.save_profile("../evil", st, tmp_path)


def test_output_sends_per_led_colors() -> None:
    server = FakeOpenRgbServer()
    st = RgbSettings(
        enabled=True,
        effect=Effect.STATIC,
        color=(0, 0, 9),
        zone_colors={"LED_C1": (9, 0, 0)},
        alert_temp=0,
    )
    out = RgbOutput(st, connect=lambda: OpenRgbClient(port=server.port))
    leds = out.render(0.0)
    out.close()
    assert leds is not None and leds[-1] == (9, 0, 0) and leds[0] == (0, 0, 9)


def test_temperature_steps_blend_and_bands() -> None:
    from caseview.rgb.effects import temperature_color

    stops = [(50.0, (0, 0, 255)), (60.0, (0, 255, 0)), (70.0, (255, 255, 0)), (80.0, (255, 0, 0))]
    assert temperature_color(stops, 30) == (0, 0, 255)
    assert temperature_color(stops, 55) == (0, 128, 128)
    assert temperature_color(stops, 95) == (255, 0, 0)
    assert temperature_color(stops, 65, blend=False) == (0, 255, 0)
    assert temperature_color(stops, 75, blend=False) == (255, 255, 0)


def test_temp_stops_config_and_old_cold_hot_migration() -> None:
    cfg = Config.from_toml(
        '[rgb]\ntemp_blend = false\ntemp_stops = [[70, "#FFFF00"], [50, "#0000FF"]]'
    )
    assert cfg.rgb.temp_stops == [(50.0, (0, 0, 255)), (70.0, (255, 255, 0))]
    assert not cfg.rgb.temp_blend
    assert Config.from_toml(cfg.to_toml()).rgb == cfg.rgb
    old = Config.from_toml('[rgb]\ncold_temp = 30\nhot_temp = 80\nhot_color = "#FF0000"')
    assert old.rgb.temp_stops == [(30.0, (0, 80, 255)), (80.0, (255, 0, 0))]
    for bad in ("temp_stops = []", 'temp_stops = [["hot", "#FF0000"]]', "temp_stops = [[50]]"):
        with pytest.raises(ConfigError):
            Config.from_toml(f"[rgb]\n{bad}\n")
