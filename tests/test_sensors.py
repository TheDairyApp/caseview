import re
from pathlib import Path

from caseview.sensors import Reading, pick_default_values
from caseview.sensors.fake import FakeProvider

SRC = Path(__file__).resolve().parents[1] / "src" / "caseview"


def r(hw_type: str, kind: str, name: str, value: float | None) -> Reading:
    return Reading(hw_type, "hw", kind, name, value)


def test_picks_package_temp_board_cpu_fan_and_gpu_core() -> None:
    readings = [
        r("Cpu", "Temperature", "P-Core #1", 60),
        r("Cpu", "Temperature", "CPU Package", 55),
        r("SuperIO", "Fan", "System Fan #1", 700),
        r("SuperIO", "Fan", "CPU Fan", 1250),
        r("SuperIO", "Fan", "CPU OPT Fan", 900),
        r("GpuNvidia", "Temperature", "GPU Hot Spot", 50),
        r("GpuNvidia", "Temperature", "GPU Core", 41),
        r("GpuNvidia", "Fan", "GPU Fan 1", 1000),  # GPU fans never feed the CPU fan slot
    ]
    assert pick_default_values(readings) == {"cpu_temp": 55, "cpu_fan": 1250, "gpu_temp": 41}


def test_unreadable_cpu_without_admin_leaves_slots_out() -> None:
    readings = [
        r("Cpu", "Temperature", "CPU Package", None),
        r("GpuNvidia", "Temperature", "GPU Core", 33),
    ]
    assert pick_default_values(readings) == {"gpu_temp": 33}


def test_cpu_fan_falls_back_to_fan_1_then_any_spinning_fan() -> None:
    assert pick_default_values([r("SuperIO", "Fan", "Fan #1", 1100)])["cpu_fan"] == 1100
    readings = [r("SuperIO", "Fan", "Fan #3", 0), r("SuperIO", "Fan", "Fan #4", 950)]
    assert pick_default_values(readings)["cpu_fan"] == 950


def test_typical_intel_desktop_readings() -> None:
    # Shape of real readings from an Intel desktop with an ITE Super I/O fan chip (as admin).
    readings = [
        r("SuperIO", "Temperature", "Temperature #1", 34),
        r("SuperIO", "Fan", "Fan #1", 1757.8),
        r("SuperIO", "Fan", "Fan #2", 0),
        r("SuperIO", "Fan", "Fan #4", 1240.8),
        r("Cpu", "Temperature", "Core Max", 36),
        r("Cpu", "Temperature", "CPU Package", 36),
        r("Cpu", "Temperature", "P-Core #1 Distance to TjMax", 67),
        r("GpuNvidia", "Temperature", "GPU Core", 33),
        r("GpuNvidia", "Fan", "GPU Fan 1", 1075),
    ]
    assert pick_default_values(readings) == {"cpu_temp": 36, "cpu_fan": 1757.8, "gpu_temp": 33}


def test_fake_provider_feeds_all_slots() -> None:
    assert set(pick_default_values(FakeProvider().read())) == {"cpu_temp", "cpu_fan", "gpu_temp"}


def test_no_fan_or_clock_control_anywhere_in_source() -> None:
    """CaseView is read-only: it must never change fan speeds or hardware settings."""
    forbidden = re.compile(r"\.Control\b|SetSoftware|SetDefault|IsControllerEnabled")
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if forbidden.search(line) and not line.lstrip().startswith(("#", '"', "(SetSoftware"))
    ]
    assert offenders == []
