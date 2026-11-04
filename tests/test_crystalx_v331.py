import pytest

from caseview.drivers import driver_for
from caseview.drivers.crystalx_v331 import CrystalXV331, fan_wire_value, shown_fan


class FakeLink:
    def __init__(self) -> None:
        self.reports: list[bytes] = []
        self.closed = False

    def write(self, report: bytes) -> int:
        self.reports.append(report)
        return len(report)

    def close(self) -> None:
        self.closed = True


# (sent, what the real panel showed) -- observed on hardware 2026-11-03
OBSERVED = [
    (3456, 3496),
    (1111, 1111),
    (2222, 2222),
    (3333, 3373),
    (4444, 4484),
    (5555, 5535),
    (6666, 6646),
    (7777, 7797),
    (8888, 8808),
    (9999, 9919),
    (1050, 1050),
    (5050, 5090),
    (9050, 9070),
    *((v, v) for v in range(10, 100, 10)),
]


@pytest.mark.parametrize(("sent", "shown"), OBSERVED)
def test_firmware_model_matches_hardware(sent: int, shown: int) -> None:
    assert shown_fan(sent) == shown


def test_fan_fix_exact_for_almost_all_values() -> None:
    wrong = [v for v in range(10000) if shown_fan(fan_wire_value(v)) != v]
    assert len(wrong) < 100  # only where rpm//10 straddles a multiple of 256
    assert all(abs(shown_fan(fan_wire_value(v)) - v) <= 90 for v in wrong)


def test_fan_fix_is_noop_below_2560() -> None:
    assert all(fan_wire_value(v) == v for v in range(2560))


def test_encode_layout() -> None:
    r = CrystalXV331(FakeLink()).encode({"cpu_temp": 12, "cpu_fan": 1234, "gpu_temp": 78})
    assert len(r) == 65
    assert r[:11] == bytes([0x00, 0x40, 12, 0, 0, 0, 0x04, 0xD2, 0, 0, 78])
    assert not any(r[11:])


def test_encode_clamps_temperatures_to_two_digits() -> None:
    r = CrystalXV331(FakeLink()).encode({"cpu_temp": 150, "cpu_fan": -5, "gpu_temp": 42.6})
    assert (r[2], r[6:8], r[10]) == (99, b"\x00\x00", 43)


def test_render_writes_and_close_closes() -> None:
    link = FakeLink()
    d = CrystalXV331(link)
    d.render({})
    d.close()
    assert len(link.reports) == 1 and link.closed


def test_registry_matches_usb_id() -> None:
    assert driver_for(0x5131, 0x2007) is CrystalXV331
    assert driver_for(0x048D, 0x5702) is None
