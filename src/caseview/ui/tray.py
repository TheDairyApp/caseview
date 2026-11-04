"""System tray app: runs the display + RGB engine in the background all day.

Menu: live status, Lighting window, lighting on/off, profiles, start with Windows,
settings folder, quit. Only one instance runs at a time.
"""

from __future__ import annotations

import copy
import ctypes
import functools
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QLockFile, QObject, QPoint, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from caseview.cli import make_provider, open_display
from caseview.core import autostart, logs
from caseview.core import config as config_mod
from caseview.core.config import SLOT_IDS, ConfigError, DisplayPage, SlotConfig
from caseview.core.engine import Engine, State, Status
from caseview.core.metrics import DISPLAY_SOURCES, SystemMetrics, trim_memory
from caseview.drivers.base import DisplayDriver
from caseview.rgb import profiles
from caseview.rgb.effects import Effect, format_color, parse_color
from caseview.rgb.output import RgbOutput
from caseview.sensors import Reading, SensorProvider
from caseview.ui.theme import apply_theme, load_theme

if TYPE_CHECKING:
    from caseview.core.web import WebServer
    from caseview.ui.dashboard import Dashboard
    from caseview.ui.display_window import DisplayWindow
    from caseview.ui.rgb_window import RgbWindow
    from caseview.ui.taskbar_bar import TaskbarBar

EFFECT_LABELS = {
    Effect.STATIC: "Static color",
    Effect.TEMPERATURE: "Temperature",
    Effect.BREATHING: "Breathing",
    Effect.COLOR_CYCLE: "Color cycle",
    Effect.RAINBOW: "Rainbow wave",
    Effect.OFF: "Off",
}

log = logging.getLogger(__name__)

STATE_TEXT = {
    State.RUNNING: "Display: live",
    State.NO_DISPLAY: "Display: not connected",
    State.WAITING_FOR_CONFLICT: "Display: paused while DT Control runs",
}


class NoSensors:
    """Used when LibreHardwareMonitor can't load; the display shows blanks instead of crashing."""

    def read(self) -> list[Reading]:
        return []

    def close(self) -> None:
        pass


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def make_icon(accent: QColor) -> QIcon:
    """Rounded square with three bars, echoing the case display's three readouts."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, accent.lighter(130))
        grad.setColorAt(1, accent.darker(130))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        p.setBrush(QColor(255, 255, 255, 235))
        bar_w, gap = size * 0.16, size * 0.1
        left = (size - 3 * bar_w - 2 * gap) / 2
        for i, h in enumerate((0.42, 0.6, 0.32)):
            x = left + i * (bar_w + gap)
            p.drawRoundedRect(
                QRectF(x, size * 0.78 - size * h, bar_w, size * h), bar_w / 3, bar_w / 3
            )
        p.end()
        icon.addPixmap(pm)
    return icon


class TrayApp(QObject):
    def __init__(
        self,
        provider: SensorProvider,
        open_display_fn: Callable[[], DisplayDriver] = open_display,
    ) -> None:
        super().__init__()
        self.config = config_mod.load()
        self.rgb = RgbOutput(copy.deepcopy(self.config.rgb))
        self.rgb.start()
        self.engine = Engine(
            provider,
            self.config,
            open_display_fn,
            rgb=self.rgb,
            metrics=SystemMetrics(power=self.config.power),
        )
        self.status = Status()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run_engine, name="engine", daemon=True)
        self._window: RgbWindow | None = None
        self._dashboard: Dashboard | None = None
        self._display_window: DisplayWindow | None = None
        self._bar: TaskbarBar | None = None
        self._web: WebServer | None = None
        self._lock = threading.RLock()  # config edits arrive from the UI and the web thread
        self._admin = is_admin()

        self.theme = load_theme()
        self.tray = QSystemTrayIcon(make_icon(QColor(*self.theme.accent)))
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

        self._refresh = QTimer(self)
        self._refresh.timeout.connect(self._update_status)
        self._refresh.start(1000)
        self._thread.start()
        self._update_status()
        if self.config.ui.stats_bar:
            self._show_bar(True)
        if self.config.ui.web_enabled:
            self._set_web(True)
        QTimer.singleShot(5000, trim_memory)  # after the bar/web start-up allocations

    # ---------------------------------------------------------------- engine

    def _run_engine(self) -> None:
        def on_status(status: Status) -> None:
            self.status = status  # read by the UI timer

        self.engine.run(self._stop, on_status)

    # ------------------------------------------------------------------ menu

    def _build_menu(self) -> None:
        m = self.menu
        title = m.addAction("CaseView")
        title.setEnabled(False)
        self.state_action = m.addAction("Starting…")
        self.state_action.setEnabled(False)
        self.values_action = m.addAction("")
        self.values_action.setEnabled(False)
        if not self._admin:
            warn = m.addAction("CPU sensors need administrator")
            warn.setEnabled(False)
        m.addSeparator()

        m.addAction("Dashboard", self.open_dashboard)
        m.addAction("Case display…", self.open_display_pages)
        self.page_menu = m.addMenu("Display page")
        self.page_menu.aboutToShow.connect(self._fill_page_menu)
        self.bar_action = QAction("Taskbar stats bar", m, checkable=True)
        self.bar_action.setChecked(self.config.ui.stats_bar)
        self.bar_action.toggled.connect(self._show_bar)
        m.addAction(self.bar_action)
        m.addSeparator()
        m.addAction("Lighting…", self.open_lighting)
        self.rgb_action = QAction("Lighting on", m, checkable=True)
        self.rgb_action.setChecked(self.config.rgb.enabled)
        self.rgb_action.toggled.connect(self._toggle_rgb)
        m.addAction(self.rgb_action)
        self.profiles_menu = m.addMenu("Lighting profile")
        self.profiles_menu.aboutToShow.connect(self._fill_profiles)
        m.addSeparator()

        phone = m.addMenu("Phone access")
        self.web_action = QAction("Allow phone access (home network)", phone, checkable=True)
        self.web_action.setChecked(self.config.ui.web_enabled)
        self.web_action.toggled.connect(self._set_web)
        phone.addAction(self.web_action)
        self.web_url_action = phone.addAction("Off")
        self.web_url_action.setEnabled(False)
        self.web_pin_action = phone.addAction("")
        self.web_pin_action.setEnabled(False)
        phone.addAction("Copy link", self._copy_web_url)
        phone.addAction("New PIN", self._new_web_pin)
        m.addSeparator()

        self.autostart_action = QAction("Start with Windows", m, checkable=True)
        self.autostart_action.setChecked(autostart.is_enabled())
        self.autostart_action.toggled.connect(self._toggle_autostart)
        m.addAction(self.autostart_action)
        m.addAction("Open settings folder", self._open_settings_folder)
        m.addSeparator()
        m.addAction("Quit", self.quit)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_dashboard()

    def _update_status(self) -> None:
        s = self.status
        state = STATE_TEXT.get(s.state, s.state.value)
        self.state_action.setText(state)
        v = self.engine.last_values
        parts = []
        if "cpu_temp" in v:
            parts.append(f"CPU {v['cpu_temp']:.0f}°C")
        if "cpu_fan" in v:
            parts.append(f"Fan {v['cpu_fan']:.0f} rpm")
        if "gpu_temp" in v:
            parts.append(f"GPU {v['gpu_temp']:.0f}°C")
        if "wall_power" in v:
            parts.append(f"≈{v['wall_power']:.0f} W / {v['wall_amps']:.2f} A")
        values = "  ·  ".join(parts) or "No sensor readings"
        self.values_action.setText(values)
        self.tray.setToolTip(f"CaseView\n{state}\n{values}")

    # ------------------------------------------------------ dashboard + bar

    def _status_text(self) -> str:
        return STATE_TEXT.get(self.status.state, self.status.state.value)

    def open_dashboard(self, anchor: QPoint | None = None) -> None:
        from caseview.ui.dashboard import Dashboard

        if self._dashboard is None:
            self._dashboard = Dashboard(
                lambda: self.engine.last_values,
                lambda: self.engine.history,
                self._status_text,
                self.theme,
                page=self._page_info,
            )
            self._dashboard.openLighting.connect(self.open_lighting)
            self._dashboard.openDisplay.connect(self.open_display_pages)
            self._dashboard.closed.connect(trim_memory)
        if self._dashboard.isVisible():
            self._dashboard.hide()
        else:
            self._dashboard.show_near(anchor or QCursor.pos())

    def _page_info(self) -> tuple[str, dict[str, str], dict[str, float | None]]:
        page = self.engine.current_page()
        name = f"{page.name} (locked)" if self.config.locked() else page.name
        return name, page.sources(), self.engine.page_values(page)

    def open_display_pages(self) -> None:
        from caseview.ui.display_window import DisplayWindow

        if self._display_window is not None and self._display_window.isVisible():
            self._display_window.raise_()
            self._display_window.activateWindow()
            return
        window = DisplayWindow(
            self.config,
            lambda: self.engine.last_values,
            lambda: self.engine.current_page().name,
            self._apply_pages,
            self.theme,
            lock=self.lock_page,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(self._display_closed)
        window.show()
        self._display_window = window

    def _fill_page_menu(self) -> None:
        menu = self.page_menu
        menu.clear()
        group = QActionGroup(menu)
        rotate = menu.addAction(f"Rotate pages (every {self.config.page_seconds:g} s)")
        rotate.setCheckable(True)
        rotate.setChecked(self.config.locked() is None)
        rotate.triggered.connect(lambda: self.lock_page(""))
        group.addAction(rotate)
        menu.addSeparator()
        for page in self.config.pages:
            action = menu.addAction(f"Lock on “{page.name}”")
            action.setCheckable(True)
            action.setChecked(self.config.locked_page == page.name)
            action.triggered.connect(functools.partial(self.lock_page, page.name))
            group.addAction(action)

    def lock_page(self, name: str) -> None:
        """Keep the display on page `name`; "" goes back to rotating. Saved immediately."""
        if name and not any(p.name == name for p in self.config.pages):
            raise ValueError(f"no display page named {name!r}")
        with self._lock:
            self.config.locked_page = name
            config_mod.save(self.config)
        if self._display_window is not None:
            self._display_window.set_locked(name)

    def _display_closed(self) -> None:
        self._display_window = None
        trim_memory()

    def _apply_pages(self, pages: list[DisplayPage], seconds: float, persist: bool) -> None:
        with self._lock:
            self.config.pages = pages
            self.config.page_seconds = seconds
            if persist:
                config_mod.save(self.config)

    def _show_bar(self, on: bool) -> None:
        from caseview.ui.taskbar_bar import TaskbarBar

        if on and self._bar is None:
            self._bar = TaskbarBar(lambda: self.engine.last_values, self.theme)
            self._bar.clicked.connect(self.open_dashboard)
            self._bar.moved.connect(self._bar_moved)
            self._bar.hideRequested.connect(lambda: self.bar_action.setChecked(False))
            self._bar.place(self.config.ui.stats_bar_pos)
            self._bar.show()
        elif not on and self._bar is not None:
            self._bar.close()
            self._bar.deleteLater()
            self._bar = None
            trim_memory()
        with self._lock:
            if self.config.ui.stats_bar != on:
                self.config.ui.stats_bar = on
                config_mod.save(self.config)

    def _bar_moved(self, pos: QPoint) -> None:
        with self._lock:
            self.config.ui.stats_bar_pos = (pos.x(), pos.y())
            config_mod.save(self.config)

    # ---------------------------------------------------------- phone access

    def _set_web(self, on: bool) -> None:
        from caseview.core.web import WebServer, new_pin

        with self._lock:
            if not self.config.ui.web_pin:
                self.config.ui.web_pin = new_pin()
            self.config.ui.web_enabled = on
            config_mod.save(self.config)
        if on and self._web is None:
            server = WebServer(self, self.config.ui.web_port, self.config.ui.web_pin)
            try:
                server.start()
            except OSError as e:
                self.web_action.blockSignals(True)
                self.web_action.setChecked(False)
                self.web_action.blockSignals(False)
                QMessageBox.warning(
                    None, "Phone access", f"Could not start on port {server.port}:\n{e}"
                )
                return
            self._web = server
        elif not on and self._web is not None:
            self._web.stop()
            self._web = None
        self._update_web_menu()

    def _update_web_menu(self) -> None:
        if self._web is None:
            self.web_url_action.setText("Off")
            self.web_pin_action.setText("")
            self.web_pin_action.setVisible(False)
        else:
            self.web_url_action.setText(f"Open on your phone: {self._web.url}")
            self.web_pin_action.setText(f"PIN: {self._web.pin}")
            self.web_pin_action.setVisible(True)

    def _copy_web_url(self) -> None:
        if self._web is not None:
            QGuiApplication.clipboard().setText(self._web.url)

    def _new_web_pin(self) -> None:
        from caseview.core.web import new_pin

        with self._lock:
            self.config.ui.web_pin = new_pin()
            config_mod.save(self.config)
        if self._web is not None:
            self._web.pin = self.config.ui.web_pin
        self._update_web_menu()

    # Controller interface for the web server (called on its threads).

    def web_state(self) -> dict[str, Any]:
        v = dict(self.engine.last_values)
        hist = self.engine.history
        readings = self.engine.readings
        with self._lock:
            rgb, slots = copy.deepcopy(self.config.rgb), dict(self.config.slots)
        temps = [[n, n] for n in sorted({r.name for r in readings if r.kind == "Temperature"})]
        fans = [[n, n] for n in sorted({r.name for r in readings if r.kind == "Fan"})]
        percent = [[k, DISPLAY_SOURCES[k]] for k in ("cpu_load", "ram_pct", "gpu_load", "disk_pct")]
        watts = [[k, label] for k, label in DISPLAY_SOURCES.items() if k.endswith("power")]
        return {
            "status": self._status_text(),
            "values": {k: round(x, 2) for k, x in v.items()},
            "history": {
                k: [round(x, 1) for x in hist.series(k)]
                for k in ("cpu_load", "gpu_load", "ram_pct", "disk_pct")
            },
            "rgb": {
                "enabled": rgb.enabled,
                "effect": rgb.effect.value,
                "color": format_color(rgb.color),
                "brightness": rgb.brightness,
                "speed": rgb.speed,
            },
            "effects": [[e.value, label] for e, label in EFFECT_LABELS.items()],
            "profiles": profiles.list_profiles(),
            "display": {
                "current": self.engine.current_page().name,
                "page_seconds": self.config.page_seconds,
                "locked": self.config.locked_page if self.config.locked() else "",
                "sources": [[k, label] for k, label in DISPLAY_SOURCES.items()],
                "pages": [
                    {
                        "name": p.name,
                        "enabled": p.enabled,
                        **p.sources(),
                        "values": {
                            s: (None if x is None else round(x, 1))
                            for s, x in self.engine.page_values(p).items()
                        },
                    }
                    for p in self.config.pages
                ],
            },
            "slots": [
                {
                    "id": "cpu_temp",
                    "label": "CPU temperature",
                    "sensor": slots.get("cpu_temp", SlotConfig()).sensor,
                    "options": temps + percent,
                },
                {
                    "id": "cpu_fan",
                    "label": "CPU fan",
                    "sensor": slots.get("cpu_fan", SlotConfig()).sensor,
                    "options": fans + watts + percent,
                },
                {
                    "id": "gpu_temp",
                    "label": "GPU temperature",
                    "sensor": slots.get("gpu_temp", SlotConfig()).sensor,
                    "options": temps + percent,
                },
            ],
        }

    def web_set_pages(self, pages: list[dict[str, Any]], page_seconds: float) -> None:
        if not pages or len(pages) > 12 or not 2 <= page_seconds <= 600:
            raise ValueError("send 1-12 pages and 2-600 seconds per page")
        parsed = []
        for item in pages:
            sources = {s: str(item.get(s, "")) for s in SLOT_IDS}
            if any(src not in DISPLAY_SOURCES for src in sources.values()):
                raise ValueError("unknown readout source")
            parsed.append(
                DisplayPage(
                    name=str(item.get("name", "Page"))[:24] or "Page",
                    enabled=bool(item.get("enabled", True)),
                    **sources,
                )
            )
        self._apply_pages(parsed, page_seconds, True)

    def web_lock_page(self, name: str) -> None:
        self.lock_page(name)

    def web_set_rgb(self, changes: dict[str, Any]) -> None:
        with self._lock:
            s = self.config.rgb
            if "enabled" in changes:
                s.enabled = bool(changes["enabled"])
            if "effect" in changes:
                s.effect = Effect(str(changes["effect"]))
            if "color" in changes:
                s.color = parse_color(str(changes["color"]))
                s.zone_colors.clear()
            if "brightness" in changes:
                s.brightness = max(0, min(100, int(changes["brightness"])))
            if "speed" in changes:
                s.speed = max(1, min(10, int(changes["speed"])))
            self.rgb.set_settings(copy.deepcopy(s))
            config_mod.save(self.config)

    def web_apply_profile(self, name: str) -> None:
        settings = profiles.load_profile(name)  # ConfigError -> ValueError -> HTTP 400
        settings.enabled = True
        with self._lock:
            settings.device = settings.device or self.config.rgb.device
            self.config.rgb = settings
            self.rgb.set_settings(copy.deepcopy(settings))
            config_mod.save(self.config)

    def web_set_slot(self, slot: str, sensor: str) -> None:
        if slot not in ("cpu_temp", "cpu_fan", "gpu_temp") or not sensor or len(sensor) > 120:
            raise ValueError("unknown slot or sensor")
        with self._lock:
            self.config.slots[slot] = SlotConfig(sensor)
            config_mod.save(self.config)

    # -------------------------------------------------------------- lighting

    def open_lighting(self) -> None:
        from caseview.ui.rgb_window import RgbWindow

        if self._window is not None and self._window.isVisible():
            self._window.raise_()
            self._window.activateWindow()
            return
        window = RgbWindow(None, output=self.rgb, values=lambda: self.engine.last_values)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(self._lighting_closed)
        window.show()
        self._window = window

    def _lighting_closed(self) -> None:
        self._window = None
        trim_memory()
        self.config = config_mod.load()  # pick up "Save as startup lighting"
        self.engine.config = self.config
        self.rgb_action.blockSignals(True)
        self.rgb_action.setChecked(self.config.rgb.enabled)
        self.rgb_action.blockSignals(False)
        self.theme = load_theme()
        self.tray.setIcon(make_icon(QColor(*self.theme.accent)))

    def _toggle_rgb(self, on: bool) -> None:
        self.config.rgb.enabled = on
        self.rgb.set_settings(copy.deepcopy(self.config.rgb))
        config_mod.save(self.config)

    def _fill_profiles(self) -> None:
        self.profiles_menu.clear()
        names = profiles.list_profiles()
        if not names:
            none = self.profiles_menu.addAction("No saved profiles")
            none.setEnabled(False)
        for name in names:
            action = self.profiles_menu.addAction(name)
            action.triggered.connect(functools.partial(self._apply_profile, name))

    def _apply_profile(self, name: str) -> None:
        try:
            settings = profiles.load_profile(name)
        except ConfigError as e:
            self.tray.showMessage("CaseView", str(e), QSystemTrayIcon.MessageIcon.Warning)
            return
        settings.enabled = True
        settings.device = settings.device or self.config.rgb.device
        self.config.rgb = settings
        self.rgb.set_settings(copy.deepcopy(settings))
        config_mod.save(self.config)
        self.rgb_action.setChecked(True)

    # --------------------------------------------------------------- general

    def _toggle_autostart(self, on: bool) -> None:
        ok, message = autostart.enable() if on else autostart.disable()
        if not ok:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not on)
            self.autostart_action.blockSignals(False)
            QMessageBox.warning(
                None,
                "Start with Windows",
                "Could not change the startup task. Run CaseView as administrator once "
                f"and try again.\n\n{message}",
            )

    def _open_settings_folder(self) -> None:
        folder = config_mod.default_config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def quit(self) -> None:
        self._refresh.stop()
        if self._web is not None:
            self._web.stop()
        if self._bar is not None:
            self._bar.close()
        self._stop.set()
        self._thread.join(timeout=5)  # engine.run closes display, RGB and sensors
        self.tray.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()


def main(fake_sensors: bool = False) -> int:
    logs.setup()
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    app.setQuitOnLastWindowClosed(False)  # closing the Lighting window keeps the tray
    app.setApplicationName("CaseView")

    lock = QLockFile(str(Path(config_mod.default_config_path().parent, "caseview.lock")))
    Path(lock.fileName()).parent.mkdir(parents=True, exist_ok=True)
    if not lock.tryLock(100):
        QMessageBox.information(None, "CaseView", "CaseView is already running in the tray.")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "CaseView", "No system tray is available.")
        return 1

    try:
        provider: SensorProvider = make_provider(fake_sensors)
    except Exception as e:  # missing DLLs, .NET errors
        log.warning("sensors unavailable: %s", e)
        provider = NoSensors()
    apply_theme_to_app(app)
    tray = TrayApp(provider)
    log.info("tray started (admin=%s, pid=%d)", tray._admin, os.getpid())
    return int(app.exec())


def apply_theme_to_app(app: QApplication) -> None:
    """Menus and message boxes follow the saved theme too."""
    from PySide6.QtWidgets import QWidget

    probe = QWidget()
    apply_theme(probe, load_theme())
    app.setStyleSheet(probe.styleSheet())
