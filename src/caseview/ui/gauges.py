"""Small painted widgets for the dashboard: ring gauge, sparkline, stat bar.

All are plain QPainter drawings (no images, no animations) and repaint only when
their value changes.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


def _round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


class RingGauge(QWidget):
    """Circular progress ring with the value in the middle (like the reference widgets)."""

    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._value: float | None = None
        self.setFixedSize(64, 64)

    def set_value(self, value: float | None) -> None:
        value = None if value is None else max(0.0, min(100.0, value))
        if value != self._value:
            self._value = value
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(5, 5, self.width() - 10, self.height() - 10)
        track = QColor(self._color)
        track.setAlpha(45)
        p.setPen(_round_pen(track, 6))
        p.drawArc(rect, 0, 360 * 16)
        if self._value is not None and self._value > 0:
            p.setPen(_round_pen(self._color, 6))
            p.drawArc(rect, 90 * 16, -int(self._value / 100 * 360 * 16))
        font = QFont(self.font())
        font.setPointSizeF(self.font().pointSizeF() + 3)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(self.palette().color(self.foregroundRole()))
        text = "–" if self._value is None else f"{self._value:.0f}"
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class Sparkline(QWidget):
    """Line + soft fill of recent values. `ceiling=None` scales to the data."""

    def __init__(
        self, color: QColor, ceiling: float | None = 100.0, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._color = color
        self._ceiling = ceiling
        self._data: list[float] = []
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        return QSize(120, 36)

    def set_data(self, data: list[float]) -> None:
        if data != self._data:
            self._data = data
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 2
        top = self._ceiling if self._ceiling else max(max(self._data), 1.0)
        step = w / (len(self._data) - 1)
        points = [
            QPointF(i * step, 1 + h - max(0.0, min(v, top)) / top * h)
            for i, v in enumerate(self._data)
        ]
        line = QPainterPath(points[0])
        for pt in points[1:]:
            line.lineTo(pt)
        fill = QPainterPath(line)
        fill.lineTo(points[-1].x(), h + 1)
        fill.lineTo(0, h + 1)
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c0, c1 = QColor(self._color), QColor(self._color)
        c0.setAlpha(90)
        c1.setAlpha(0)
        grad.setColorAt(0, c0)
        grad.setColorAt(1, c1)
        p.fillPath(fill, grad)
        p.setPen(QPen(self._color, 1.6))
        p.drawPath(line)


class StatBar(QWidget):
    """Thin horizontal bar (0..1) for temperatures and fans."""

    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._frac = 0.0
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fraction(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        if abs(frac - self._frac) > 0.002:
            self._frac = frac
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        track = QColor(self._color)
        track.setAlpha(40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, 3, 3)
        if self._frac > 0:
            p.setBrush(self._color)
            p.drawRoundedRect(QRectF(r.x(), r.y(), r.width() * self._frac, r.height()), 3, 3)
