"""Compact always-on-top stats strip for the taskbar area (CPU / GPU / RAM / fan).

Windows 11 has no API for third-party taskbar widgets, so this is a small frameless
tool window (no taskbar button). It starts just above the taskbar near the clock;
drag it anywhere (including onto the taskbar) and the position is remembered.
Painted text only, updated once a second.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QMenu, QWidget

from caseview.ui.theme import Theme, palette_for

ITEMS = [  # (label, value key, secondary key, unit, secondary unit, color)
    ("CPU", "cpu_load", "cpu_temp", "%", "°", QColor(64, 156, 255)),
    ("GPU", "gpu_load", "gpu_temp", "%", "°", QColor(175, 110, 255)),
    ("RAM", "ram_pct", None, "%", "", QColor(52, 199, 89)),
    ("FAN", "cpu_fan", None, "", "", QColor(48, 209, 200)),
    ("PWR", "wall_power", None, "W", "", QColor(255, 159, 10)),
]


class TaskbarBar(QWidget):
    clicked = Signal(QPoint)
    moved = Signal(QPoint)
    hideRequested = Signal()

    def __init__(self, values: Callable[[], dict[str, float]], theme: Theme) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._values = values
        self._text: list[tuple[str, str, QColor]] = []
        self._drag_from: QPoint | None = None
        self._dragged = False
        self.set_theme(theme)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def set_theme(self, theme: Theme) -> None:
        p = palette_for(theme)
        self._bg = p.card.toRgb()
        self._bg.setAlpha(235)
        self._fg, self._muted, self._border = p.text, p.muted, p.border
        self.update()

    def _font(self, bold: bool = False, small: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(7.5 if small else 9.5)
        f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return f

    def refresh(self) -> None:
        v = self._values()
        text = []
        for label, key, second, unit, unit2, color in ITEMS:
            if key not in v:
                continue
            value = f"{v[key]:.0f}{unit}"
            if second and second in v:
                value += f" {v[second]:.0f}{unit2}"
            text.append((label, value, color))
        if text != self._text:
            self._text = text
            self.setFixedSize(self.sizeHint())
            self.update()

    def sizeHint(self) -> QSize:
        # Mirrors paintEvent: 12 px left pad, then per item dot(10) + label + 4 + value + 12.
        small, bold = QFontMetrics(self._font(small=True)), QFontMetrics(self._font(bold=True))
        width = 12.0
        for label, value, _ in self._text:
            width += 10 + small.horizontalAdvance(label) + 4 + bold.horizontalAdvance(value) + 12
        height = max(bold.height() + 14, 30)
        return QSize(max(int(width) + 2, 80), height)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(self._border, 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        x = 12.0
        small, bold = self._font(small=True), self._font(bold=True)
        if not self._text:
            p.setFont(small)
            p.setPen(self._muted)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "CaseView")
            return
        for label, value, color in self._text:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(x, rect.center().y() - 3, 6, 6))
            x += 10
            p.setFont(small)
            p.setPen(self._muted)
            lw = QFontMetrics(small).horizontalAdvance(label)
            p.drawText(QRectF(x, 0, lw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, label)
            x += lw + 4
            p.setFont(bold)
            p.setPen(self._fg)
            vw = QFontMetrics(bold).horizontalAdvance(value)
            p.drawText(QRectF(x, 0, vw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, value)
            x += vw + 12

    # -- placement and dragging --------------------------------------------

    def place(self, pos: tuple[int, int] | None) -> None:
        if pos is not None:
            point = QPoint(*pos)
            if any(
                s.availableVirtualGeometry().contains(point) or s.geometry().contains(point)
                for s in QGuiApplication.screens()
            ):
                self.move(point)
                return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 16, area.bottom() - self.height() - 8)

    def _reset_position(self) -> None:
        self.place(None)
        self.moved.emit(self.pos())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            self._dragged = True

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragged:
                self.moved.emit(self.pos())
            else:
                self.clicked.emit(event.globalPosition().toPoint())
            self._drag_from = None
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            menu.addAction("Reset position", self._reset_position)
            menu.addAction("Hide stats bar", self.hideRequested.emit)
            menu.exec(event.globalPosition().toPoint())
