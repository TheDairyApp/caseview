"""Keeps an OpenRGB connection alive and renders effects. Failures never stop the app.

Without `start()`, every `update()` renders one frame (used by the 1 s engine loop
and tests). With `start()`, a background thread renders animated effects at
~30 fps and static ones whenever their color changes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from caseview.rgb.effects import Color, RgbSettings, compute_leds
from caseview.rgb.openrgb_client import Controller, OpenRgbClient, OpenRgbError

log = logging.getLogger(__name__)

RETRY_S = 10.0
RESEND_S = 10.0  # re-send unchanged colors so OpenRGB profile reloads don't stick
ANIMATION_FPS = 30
IDLE_POLL_S = 0.2


class RgbOutput:
    def __init__(
        self,
        settings: RgbSettings,
        connect: Callable[[], OpenRgbClient] = OpenRgbClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._connect = connect
        self._clock = clock
        self._lock = threading.Lock()
        self._values: dict[str, float] = {}
        self._client: OpenRgbClient | None = None
        self._controller: Controller | None = None
        self._next_try = 0.0
        self._last: list[Color] | None = None
        self._last_sent_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error = ""

    # -- inputs ---------------------------------------------------------------

    @property
    def settings(self) -> RgbSettings:
        return self._settings

    def set_settings(self, settings: RgbSettings) -> None:
        with self._lock:
            reconnect = settings.device != self._settings.device
            self._settings = settings
            self._last = None
            if reconnect:
                self._next_try = 0.0
                self._disconnect()

    def update(self, values: dict[str, float], now: float) -> Color | None:
        with self._lock:
            self._values = dict(values)
        if self._thread is None:
            frame = self.render(now)
            return frame[0] if frame else None
        return self.current_color()

    # -- info for UIs ---------------------------------------------------------

    @property
    def controller(self) -> Controller | None:
        return self._controller

    def current_color(self) -> Color | None:
        last = self._last
        return last[0] if last else None

    # -- rendering ------------------------------------------------------------

    def render(self, now: float) -> list[Color] | None:
        with self._lock:
            s = self._settings
            if not s.enabled:
                return None
            if self._client is None and not self._open(now):
                return None
            assert self._client is not None and self._controller is not None
            zones = [(z.name, z.led_count) for z in self._controller.zones]
            leds = compute_leds(s, zones, self._values, now)
            # Controllers whose zone sizes don't add up get one color everywhere.
            if len(leds) != self._controller.led_count:
                leds = (leds[:1] or [(0, 0, 0)]) * self._controller.led_count
            if leds == self._last and now - self._last_sent_at < RESEND_S:
                return leds
            try:
                self._client.set_leds(self._controller, leds)
            except OpenRgbError as e:
                self._fail(now, str(e))
                return None
            self._last, self._last_sent_at = leds, now
            return leds

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="rgb", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.render(self._clock())
            self._stop.wait(1 / ANIMATION_FPS if self._settings.animated else IDLE_POLL_S)

    # -- connection -----------------------------------------------------------

    def _open(self, now: float) -> bool:
        if now < self._next_try:
            return False
        try:
            client = self._connect()
            wanted = self._settings.device.lower()
            matches = [c for c in client.controllers() if wanted in c.name.lower()]
            if not matches:
                client.close()
                raise OpenRgbError(f"no OpenRGB device matching {self._settings.device!r}")
            client.set_custom_mode(matches[0])
        except OpenRgbError as e:
            self._fail(now, str(e))
            return False
        self._client, self._controller = client, matches[0]
        self._last = None
        self.error = ""
        log.info("RGB connected: %s (%d LEDs)", matches[0].name, matches[0].led_count)
        return True

    def _fail(self, now: float, message: str) -> None:
        if message != self.error:
            log.warning("RGB unavailable: %s", message)
        self.error = message
        self._disconnect()
        self._next_try = now + RETRY_S

    def _disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = self._controller = None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            self._disconnect()
