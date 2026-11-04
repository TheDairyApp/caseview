"""Dashboard popup above the tray: CPU / GPU / RAM / disk rings with history,
temperatures and fans, network. Repaints once a second only while visible.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from caseview.core.metrics import History, human_rate
from caseview.ui.display_preview import CaseDisplayPreview
from caseview.ui.gauges import RingGauge, Sparkline, StatBar
from caseview.ui.theme import Theme, apply_theme

BLUE, PURPLE, GREEN, ORANGE, TEAL, PINK = (
    QColor(64, 156, 255),
    QColor(175, 110, 255),
    QColor(52, 199, 89),
    QColor(255, 159, 10),
    QColor(48, 209, 200),
    QColor(255, 85, 120),
)


def _label(text: str, name: str | None = None) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


class MetricCard(QFrame):
    def __init__(self, title: str, key: str, color: QColor, ceiling: float | None = 100) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        self.ring = RingGauge(color)
        layout.addWidget(self.ring)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label(title, "CardTitle"))
        self.detail = _label("–", "Muted")
        col.addWidget(self.detail)
        self.spark = Sparkline(color, ceiling)
        col.addWidget(self.spark)
        layout.addLayout(col, 1)

    def update_from(self, value: float | None, detail: str, history: list[float]) -> None:
        self.ring.set_value(value)
        self.detail.setText(detail)
        self.spark.set_data(history)


class StatRow(QWidget):
    def __init__(self, title: str, color: QColor, top: float, unit: str) -> None:
        super().__init__()
        self._top, self._unit = top, unit
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(4)
        grid.addWidget(_label(title, "Muted"), 0, 0)
        self.value = _label("–")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.value, 0, 1)
        self.bar = StatBar(color)
        grid.addWidget(self.bar, 1, 0, 1, 2)

    def set(self, v: float | None) -> None:
        self.value.setText("–" if v is None else f"{v:.0f}{self._unit}")
        self.bar.set_fraction(0 if v is None else v / self._top)


class Dashboard(QWidget):
    openLighting = Signal()
    openDisplay = Signal()
    closed = Signal()

    def __init__(
        self,
        values: Callable[[], dict[str, float]],
        history: Callable[[], History],
        status: Callable[[], str],
        theme: Theme,
        page: Callable[[], tuple[str, dict[str, str], dict[str, float | None]]] | None = None,
    ) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._values, self._history, self._status = values, history, status
        self._page = page
        self.setFixedWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(_label("System", "Title"))
        head.addStretch()
        self.status_label = _label("", "Pill")
        head.addWidget(self.status_label)
        root.addLayout(head)

        display = QFrame()
        display.setObjectName("Card")
        dl = QVBoxLayout(display)
        dl.setContentsMargins(14, 12, 14, 12)
        dl.setSpacing(8)
        dhead = QHBoxLayout()
        dhead.addWidget(_label("Case display", "CardTitle"))
        dhead.addStretch()
        edit_pages = QPushButton("Edit pages…")
        edit_pages.setObjectName("Ghost")
        edit_pages.clicked.connect(self._open_display)
        dhead.addWidget(edit_pages)
        dl.addLayout(dhead)
        self.display_preview = CaseDisplayPreview()
        dl.addWidget(self.display_preview)
        root.addWidget(display)
        display.setVisible(page is not None)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards = [
            MetricCard("CPU", "cpu_load", BLUE),
            MetricCard("GPU", "gpu_load", PURPLE),
            MetricCard("Memory", "ram_pct", GREEN),
            MetricCard("Disk C:", "disk_pct", ORANGE),
        ]
        for i, card in enumerate(self.cards):
            grid.addWidget(card, i // 2, i % 2)
        root.addLayout(grid)

        sensors = QFrame()
        sensors.setObjectName("Card")
        sl = QGridLayout(sensors)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.setHorizontalSpacing(18)
        sl.setVerticalSpacing(10)
        sl.addWidget(_label("Temperatures & fans", "CardTitle"), 0, 0, 1, 2)
        self.rows = {
            "cpu_temp": StatRow("CPU", PINK, 100, " °C"),
            "gpu_temp": StatRow("GPU", PURPLE, 100, " °C"),
            "gpu_hotspot": StatRow("GPU hot spot", ORANGE, 110, " °C"),
            "cpu_fan": StatRow("CPU fan", TEAL, 3000, " rpm"),
        }
        for i, row in enumerate(self.rows.values()):
            sl.addWidget(row, 1 + i // 2, i % 2)
        root.addWidget(sensors)

        net = QFrame()
        net.setObjectName("Card")
        nl = QGridLayout(net)
        nl.setContentsMargins(14, 12, 14, 12)
        nl.addWidget(_label("Network", "CardTitle"), 0, 0)
        self.down = _label("↓ –")
        self.up = _label("↑ –")
        nl.addWidget(self.down, 0, 1)
        nl.addWidget(self.up, 0, 2)
        self.net_spark = Sparkline(TEAL, None)
        nl.addWidget(self.net_spark, 1, 0, 1, 3)
        root.addWidget(net)

        power = QFrame()
        power.setObjectName("Card")
        pl = QGridLayout(power)
        pl.setContentsMargins(14, 12, 14, 12)
        pl.setHorizontalSpacing(18)
        pl.setVerticalSpacing(6)
        pl.addWidget(_label("Power", "CardTitle"), 0, 0)
        self.power_note = _label("", "Muted")
        self.power_note.setAlignment(Qt.AlignmentFlag.AlignRight)
        pl.addWidget(self.power_note, 0, 1, 1, 3)
        self.power_cells: dict[str, QLabel] = {}
        for i, (key, title) in enumerate(
            [
                ("cpu_power", "CPU"),
                ("gpu_power", "GPU"),
                ("total_power", "PC total"),
                ("wall_power", "Wall"),
            ]
        ):
            pl.addWidget(_label(title, "Muted"), 1, i)
            cell = _label("–")
            cell.setObjectName("Big")
            pl.addWidget(cell, 2, i)
            self.power_cells[key] = cell
        self.power_spark = Sparkline(ORANGE, None)
        pl.addWidget(self.power_spark, 3, 0, 1, 4)
        root.addWidget(power)

        foot = QHBoxLayout()
        foot.addStretch()
        lighting = QPushButton("Lighting…")
        lighting.clicked.connect(self._open_lighting)
        foot.addWidget(lighting)
        root.addLayout(foot)

        apply_theme(self, theme)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def _open_display(self) -> None:
        self.hide()
        self.openDisplay.emit()

    def _open_lighting(self) -> None:
        self.hide()
        self.openLighting.emit()

    def refresh(self) -> None:
        v, hist = self._values(), self._history()
        for card in self.cards:
            value = v.get(card.key)
            if card.key == "ram_pct" and "ram_used_gb" in v:
                detail = f"{v['ram_used_gb']:.1f} / {v['ram_total_gb']:.0f} GB"
            elif card.key == "disk_pct" and "disk_free_gb" in v:
                detail = f"{v['disk_free_gb']:.0f} GB free"
            elif card.key == "cpu_load" and "cpu_temp" in v:
                detail = f"{v['cpu_temp']:.0f} °C"
            elif card.key == "gpu_load" and "gpu_temp" in v:
                detail = f"{v['gpu_temp']:.0f} °C"
            else:
                detail = "" if value is not None else "unavailable"
            card.update_from(value, detail, hist.series(card.key))
        for key, row in self.rows.items():
            row.set(v.get(key))
        self.down.setText(f"↓ {human_rate(v.get('net_down', 0))}")
        self.up.setText(f"↑ {human_rate(v.get('net_up', 0))}")
        self.net_spark.set_data(hist.series("net_down"))
        amps = {"cpu_power": "cpu_amps", "gpu_power": "gpu_amps", "wall_power": "wall_amps"}
        for key, cell in self.power_cells.items():
            if key not in v:
                cell.setText("–")
                continue
            amp = amps.get(key)
            extra = ""
            if amp and amp in v:
                extra = f"  {v[amp]:.2f} A" if key == "wall_power" else f"  {v[amp]:.1f} A"
            cell.setText(f"{v[key]:.0f} W{extra}")
        self.power_note.setText(
            "CPU & GPU measured · total & wall estimated" if "total_power" in v else ""
        )
        self.power_spark.set_data(hist.series("wall_power"))
        self.status_label.setText(self._status())
        if self._page is not None:
            name, sources, page_values = self._page()
            self.display_preview.set_page(f"Page: {name}", sources, page_values)

    def show_near(self, anchor: QPoint | None = None) -> None:
        """Open above the tray (bottom-right of the screen holding `anchor`)."""
        self.refresh()
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor) if anchor else None
        area = (screen or QGuiApplication.primaryScreen()).availableGeometry()
        x = area.right() - self.width() - 12
        y = area.bottom() - self.height() - 12
        self.move(x, y)
        self.show()
        self.activateWindow()

    def showEvent(self, event: QShowEvent) -> None:
        self._timer.start(1000)
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()  # zero work while closed
        super().hideEvent(event)
        self.closed.emit()
