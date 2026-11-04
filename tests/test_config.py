from pathlib import Path

import pytest

from caseview.core import config
from caseview.core.config import Config, ConfigError, OnConflict, SlotConfig
from caseview.sensors import Reading, pick_values


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.on_conflict is OnConflict.WAIT
    assert cfg.sensor_for("cpu_temp") == "auto"


def test_round_trip(tmp_path: Path) -> None:
    cfg = Config(OnConflict.STOP, 5.0, 3.0, {"cpu_fan": SlotConfig("Fan #4")})
    path = config.save(cfg, tmp_path / "sub" / "config.toml")
    assert config.load(path) == cfg


@pytest.mark.parametrize(
    "text",
    [
        "not toml [",
        '[display]\non_conflict = "kill"',
        "[display]\nreconnect_interval_s = 0",
        "[slots.cpu_temp]\nsensor = 5",
        '[slots.cpu_temp]\nsensor = ""',
    ],
)
def test_invalid_config_is_rejected(text: str) -> None:
    with pytest.raises(ConfigError):
        Config.from_toml(text)


def _readings() -> list[Reading]:
    return [
        Reading("Cpu", "i7", "Temperature", "CPU Package", 40),
        Reading("Cpu", "i7", "Temperature", "Core Max", 47),
        Reading("SuperIO", "ITE Super I/O", "Fan", "Fan #1", 1750),
        Reading("SuperIO", "ITE Super I/O", "Fan", "Fan #4", 1240),
        Reading("GpuNvidia", "Example GPU", "Temperature", "GPU Core", 33),
    ]


def test_configured_sensor_by_name_and_by_key() -> None:
    choices = {"cpu_temp": "Core Max", "cpu_fan": "ITE Super I/O/Fan/Fan #4", "gpu_temp": "auto"}
    assert pick_values(_readings(), choices) == {"cpu_temp": 47, "cpu_fan": 1240, "gpu_temp": 33}


def test_configured_sensor_missing_blanks_slot_instead_of_guessing() -> None:
    assert "cpu_fan" not in pick_values(_readings(), {"cpu_fan": "Pump"})
