"""The update loop: read sensors, render to the display, survive unplug/sleep and DT Control."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from caseview.core import conflicts
from caseview.core.config import Config, DisplayPage, OnConflict
from caseview.core.metrics import DISPLAY_SOURCES, History, SystemMetrics, trim_memory
from caseview.drivers.base import DisplayDriver
from caseview.rgb.effects import Color
from caseview.rgb.output import RgbOutput
from caseview.sensors import Reading, SensorProvider, pick_values
from caseview.transport.hid import HidError

log = logging.getLogger(__name__)

CONFLICT_CHECK_INTERVAL_S = 5.0


class State(StrEnum):
    RUNNING = "running"
    NO_DISPLAY = "no_display"
    WAITING_FOR_CONFLICT = "waiting_for_conflict"


@dataclass
class Status:
    state: State = State.NO_DISPLAY
    values: dict[str, float] = field(default_factory=dict)
    detail: str = ""
    rgb: Color | None = None
    page: str = ""  # name of the display page being shown


class Engine:
    def __init__(
        self,
        provider: SensorProvider,
        config: Config,
        open_display: Callable[[], DisplayDriver],
        find_conflicts: Callable[[], list[str]] = conflicts.running_conflicts,
        stop_app: Callable[[str], bool] = conflicts.stop_app,
        clock: Callable[[], float] = time.monotonic,
        rgb: RgbOutput | None = None,
        metrics: SystemMetrics | None = None,
    ) -> None:
        self._provider = provider
        self.config = config
        self._open_display = open_display
        self._find_conflicts = find_conflicts
        self._stop_app = stop_app
        self._clock = clock
        self._rgb = rgb
        self._driver: DisplayDriver | None = None
        self._next_open = 0.0
        self._next_conflict_check = 0.0
        self._conflicts: list[str] = []
        self.status = Status()
        self.last_values: dict[str, float] = {}  # latest sensor picks, even without a display
        self.history = History()
        self._metrics = metrics
        self._readings: list[Reading] = []
        self._next_read = 0.0
        self._trimmed = False

    @property
    def readings(self) -> list[Reading]:
        return self._readings

    @property
    def interval_s(self) -> float:
        return self._driver.update_interval_s if self._driver else 1.0

    def step(self) -> Status:
        now = self._clock()
        if now >= self._next_conflict_check:
            self._next_conflict_check = now + CONFLICT_CHECK_INTERVAL_S
            self._conflicts = self._handle_conflicts(self._find_conflicts())

        # Sensors and RGB keep working even while the display is missing or taken.
        # Hardware sensors are the expensive part: read them every sensor_interval_s and
        # reuse the last readings in between (the display still refreshes every second).
        if now >= self._next_read:
            self._readings = self._provider.read()
            self._next_read = now + self.config.sensor_interval_s
        # `slots` picks which hardware sensor feeds cpu_temp / cpu_fan / gpu_temp.
        sensor_choices = {
            k: v.sensor for k, v in self.config.slots.items() if v.sensor not in DISPLAY_SOURCES
        }
        values = pick_values(self._readings, sensor_choices)
        if self._metrics is not None:
            self._metrics.power = self.config.power
            values = {**self._metrics.sample(self._readings), **values}
        self.last_values = values
        page = self.current_page(now)
        shown = {slot: values[src] for slot, src in page.sources().items() if src in values}
        self.history.add(values)
        if not self._trimmed:
            self._trimmed = True
            trim_memory()  # drop start-up allocations (DLL loading, first sensor scan)
        rgb = self._rgb.update(values, now) if self._rgb else None
        self._page_name = page.name

        if self._conflicts:
            self._release()
            names = ", ".join(conflicts.describe(c) for c in self._conflicts)
            return self._set(State.WAITING_FOR_CONFLICT, f"{names} is using the display", rgb)

        if self._driver is None:
            if now < self._next_open:
                return self._set(self.status.state, self.status.detail, rgb)
            try:
                self._driver = self._open_display()
                log.info("display connected: %s", self._driver.name)
            except HidError as e:
                self._next_open = now + self.config.reconnect_interval_s
                return self._set(State.NO_DISPLAY, str(e), rgb)

        try:
            self._driver.render(shown)
        except HidError as e:
            log.warning("display write failed, will reconnect: %s", e)
            self._release()
            self._next_open = now + self.config.reconnect_interval_s
            return self._set(State.NO_DISPLAY, str(e), rgb)
        self.status = Status(State.RUNNING, shown, self._driver.name, rgb, page.name)
        return self.status

    def current_page(self, now: float | None = None) -> DisplayPage:
        pages = self.config.enabled_pages()
        t = self._clock() if now is None else now
        return pages[int(t // self.config.page_seconds) % len(pages)]

    def page_values(self, page: DisplayPage) -> dict[str, float | None]:
        """What `page` would show right now (for previews in the windows and phone page)."""
        return {slot: self.last_values.get(src) for slot, src in page.sources().items()}

    def run(self, stop: threading.Event, on_status: Callable[[Status], None] | None = None) -> None:
        try:
            while not stop.is_set():
                status = self.step()
                if on_status:
                    on_status(status)
                stop.wait(self.interval_s)
        finally:
            self.close()

    def close(self) -> None:
        self._release()
        if self._rgb:
            self._rgb.close()
        self._provider.close()

    def _handle_conflicts(self, found: list[str]) -> list[str]:
        if not found or self.config.on_conflict is not OnConflict.STOP:
            return found
        remaining = []
        for app in found:
            if self._stop_app(app):
                log.info("stopped %s", conflicts.describe(app))
            else:
                log.warning("could not stop %s (needs admin?)", conflicts.describe(app))
                remaining.append(app)
        return remaining

    def _release(self) -> None:
        if self._driver is not None:
            with contextlib.suppress(HidError):
                self._driver.close()
            self._driver = None

    def _set(self, state: State, detail: str, rgb: Color | None = None) -> Status:
        if (state, detail) != (self.status.state, self.status.detail):
            log.info("%s: %s", state.value, detail)
        self.status = Status(state, {}, detail, rgb, getattr(self, "_page_name", ""))
        return self.status
