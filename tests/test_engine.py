from caseview.core.config import Config, OnConflict
from caseview.core.engine import Engine, State
from caseview.drivers.base import DisplayDriver
from caseview.drivers.crystalx_v331 import CrystalXV331
from caseview.sensors.fake import FakeProvider
from caseview.transport.hid import HidError


class FlakyLink:
    def __init__(self, fail_writes: int = 0) -> None:
        self.fail_writes = fail_writes
        self.writes = 0
        self.reports: list[bytes] = []
        self.closed = False

    def write(self, report: bytes) -> int:
        if self.fail_writes:
            self.fail_writes -= 1
            raise HidError("device unplugged")
        self.writes += 1
        self.reports.append(report)
        return len(report)

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(self, config: Config | None = None) -> None:
        self.now = 0.0
        self.display_present = True
        self.links: list[FlakyLink] = []
        self.conflicts: list[str] = []
        self.stopped: list[str] = []
        self.can_stop = True
        self.engine = Engine(
            FakeProvider(),
            config or Config(reconnect_interval_s=3.0),
            self.open_display,
            find_conflicts=lambda: list(self.conflicts),
            stop_app=self.stop_app,
            clock=lambda: self.now,
        )

    def open_display(self) -> DisplayDriver:
        if not self.display_present:
            raise HidError("no supported display found")
        link = FlakyLink()
        self.links.append(link)
        return CrystalXV331(link)

    def stop_app(self, image: str) -> bool:
        if self.can_stop:
            self.stopped.append(image)
            self.conflicts.remove(image)
        return self.can_stop

    def tick(self, seconds: float = 1.0) -> State:
        state = self.engine.step().state
        self.now += seconds
        return state


def test_runs_and_writes_every_tick() -> None:
    from caseview.core.config import DisplayPage

    h = Harness(Config(pages=[DisplayPage("Temp", "cpu_temp", "cpu_fan", "gpu_temp")]))
    assert [h.tick() for _ in range(3)] == [State.RUNNING] * 3
    assert h.links[0].writes == 3
    assert set(h.engine.status.values) == {"cpu_temp", "cpu_fan", "gpu_temp"}


def test_waits_for_display_and_retries_at_interval() -> None:
    h = Harness()
    h.display_present = False
    assert h.tick() is State.NO_DISPLAY
    h.display_present = True
    assert h.tick() is State.NO_DISPLAY  # 1 s later: still inside the 3 s back-off
    h.tick()
    assert h.tick() is State.RUNNING


def test_reconnects_after_write_failure() -> None:
    h = Harness()
    h.tick()
    h.links[0].fail_writes = 1
    assert h.tick() is State.NO_DISPLAY
    assert h.links[0].closed
    for _ in range(3):
        h.tick()
    assert h.engine.status.state is State.RUNNING and len(h.links) == 2


def test_wait_policy_releases_display_while_dt_control_runs() -> None:
    h = Harness()
    h.tick()
    h.conflicts = ["LCD-CS.exe"]
    h.now += 5  # next conflict check
    assert h.tick() is State.WAITING_FOR_CONFLICT
    assert h.links[0].closed and h.stopped == []
    h.conflicts = []
    h.now += 5
    assert h.tick() is State.RUNNING


def test_stop_policy_closes_dt_control() -> None:
    h = Harness(Config(on_conflict=OnConflict.STOP))
    h.conflicts = ["LCD-CS.exe"]
    assert h.tick() is State.RUNNING
    assert h.stopped == ["LCD-CS.exe"]


def test_stop_policy_falls_back_to_waiting_without_admin() -> None:
    h = Harness(Config(on_conflict=OnConflict.STOP))
    h.can_stop = False
    h.conflicts = ["LCD-CS.exe"]
    assert h.tick() is State.WAITING_FOR_CONFLICT


class CountingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def read(self):  # type: ignore[no-untyped-def]
        self.reads += 1
        return super().read()


def test_hardware_sensors_are_read_every_sensor_interval_not_every_tick() -> None:
    h = Harness(Config(sensor_interval_s=2.0))
    provider = CountingProvider()
    h.engine._provider = provider
    for _ in range(6):
        h.tick(1.0)
    assert provider.reads == 3 and h.links[0].writes == 6


def test_metrics_and_history_are_bounded() -> None:
    from caseview.core.metrics import History, SystemMetrics

    values = SystemMetrics().sample([])
    assert {"cpu_load", "ram_pct", "net_down", "net_up"} <= set(values)
    hist = History(length=5)
    for i in range(20):
        hist.add({"cpu_load": float(i)})
    assert hist.series("cpu_load") == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_power_estimates_and_amps() -> None:
    from caseview.core.config import PowerSettings
    from caseview.core.metrics import power_estimates

    p = PowerSettings(mains_voltage=230, psu_efficiency=0.9, other_watts=50)
    out = power_estimates({"cpu_power": 120.0, "gpu_power": 240.0}, p)
    assert out["cpu_amps"] == 10.0 and out["gpu_amps"] == 20.0
    assert out["total_power"] == 410.0
    assert round(out["wall_power"], 1) == 455.6 and round(out["wall_amps"], 2) == 1.98
    assert power_estimates({}, p) == {}


def test_zero_cpu_power_without_admin_is_ignored() -> None:
    from caseview.core.metrics import SystemMetrics
    from caseview.sensors import Reading

    v = SystemMetrics().sample(
        [
            Reading("Cpu", "i7", "Power", "CPU Package", 0.0),
            Reading("GpuNvidia", "Example GPU", "Power", "GPU Package", 8.4),
        ]
    )
    assert "cpu_power" not in v and v["gpu_power"] == 8.4 and round(v["gpu_amps"], 3) == 0.7


def test_display_slots_can_show_watts_cpu_and_ram_percent() -> None:
    from caseview.core.metrics import SystemMetrics
    from caseview.drivers.crystalx_v331 import shown_fan

    cfg = Config.from_toml(  # old-style metric slots become a "Custom" page
        '[slots.cpu_temp]\nsensor = "cpu_load"\n[slots.cpu_fan]\nsensor = "wall_power"\n'
        '[slots.gpu_temp]\nsensor = "ram_pct"\n'
    )
    assert [p.name for p in cfg.pages] == ["Custom"]
    h = Harness(cfg)
    h.engine._metrics = SystemMetrics()
    h.engine._metrics.sample = lambda readings: {  # type: ignore[method-assign]
        "cpu_load": 100.0,
        "ram_pct": 57.4,
        "wall_power": 3456.0,
    }
    h.tick()
    v = h.engine.status.values
    assert (v["cpu_temp"], v["cpu_fan"], v["gpu_temp"]) == (100.0, 3456.0, 57.4)
    report = h.links[0].reports[-1]
    assert report[2] == 99  # 100 % clamps to the 2-digit readout
    assert report[10] == 57
    assert shown_fan(int.from_bytes(report[6:8], "big")) == 3456  # 4-digit watts, fan-digit fix


def test_power_config_validation() -> None:
    import pytest

    from caseview.core.config import ConfigError

    cfg = Config.from_toml("[power]\nmains_voltage = 120\nother_watts = 70")
    assert (cfg.power.mains_voltage, cfg.power.other_watts) == (120.0, 70.0)
    assert Config.from_toml(cfg.to_toml()).power == cfg.power
    with pytest.raises(ConfigError):
        Config.from_toml("[power]\npsu_efficiency = 2")


def test_pages_rotate_and_default_layout() -> None:
    from caseview.core.config import default_pages

    assert [p.name for p in default_pages()] == ["Power", "CPU", "GPU", "Temp"]
    power = default_pages()[0]
    assert (power.cpu_fan, power.cpu_temp, power.gpu_temp) == (
        "total_power",
        "cpu_amps",
        "gpu_amps",
    )

    cfg = Config(page_seconds=5)
    cfg.pages[1].enabled = False  # skip the CPU page
    h = Harness(cfg)
    seen = []
    for _ in range(4):
        seen.append(h.engine.current_page(h.now).name)
        h.now += 5
    assert seen == ["Power", "GPU", "Temp", "Power"]


def test_pages_config_round_trip_and_validation() -> None:
    import pytest

    from caseview.core.config import ConfigError

    cfg = Config.from_toml(
        '[display]\npage_seconds = 8\n[[pages]]\nname = "Mine"\ncpu_temp = "ram_pct"\n'
        'cpu_fan = "gpu_clock"\ngpu_temp = "gpu_load"\n'
    )
    assert cfg.page_seconds == 8 and cfg.pages[0].sources()["cpu_fan"] == "gpu_clock"
    assert Config.from_toml(cfg.to_toml()) == cfg
    with pytest.raises(ConfigError):
        Config.from_toml("pages = []")
    with pytest.raises(ConfigError):
        Config.from_toml("[display]\npage_seconds = 0")


def test_locked_page_stays_even_when_switched_off_and_unknown_lock_rotates() -> None:
    cfg = Config(page_seconds=5)
    cfg.pages[2].enabled = False
    cfg.locked_page = cfg.pages[2].name  # "GPU", switched off for rotation
    h = Harness(cfg)
    assert {h.engine.current_page(t).name for t in (0, 5, 10, 15)} == {"GPU"}
    cfg.locked_page = "Renamed away"
    assert h.engine.current_page(0).name == "Power"
    assert Config.from_toml(Config(locked_page="CPU").to_toml()).locked_page == "CPU"
