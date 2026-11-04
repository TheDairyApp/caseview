"""Custom widgets: color wheel, color chips, toggle switch, value slider, previews.

Painted widgets take their accent from the palette's Highlight color, which the theme sets.
Expensive drawings (wheel, temperature bar) are cached as pixmaps and only redrawn
when their size or data changes, so dragging stays smooth.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSlider,
    QWidget,
)

from caseview.rgb.effects import Color, TempStop, temperature_color


def to_qcolor(c: Color) -> QColor:
    return QColor(*c)


def from_qcolor(q: QColor) -> Color:
    return q.red(), q.green(), q.blue()


def _accent(w: QWidget) -> QColor:
    return w.palette().color(QPalette.ColorRole.Highlight)


def _is_dark(w: QWidget) -> bool:
    return w.palette().color(QPalette.ColorRole.Window).lightness() < 128


class _Rounded:
    _radius = 10

    def set_radius(self, radius: int) -> None:
        self._radius = radius
        self.update()  # type: ignore[attr-defined]


# --------------------------------------------------------------- color wheel


class ColorWheel(_Rounded, QWidget):
    """Hue around the circle, saturation from the centre out. Brightness is a separate slider."""

    hueSatChanged = Signal(float, float)  # hue 0..1, saturation 0..1
    MAX_DIAMETER = 520  # the widget fills its space; the circle stays centered and capped

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.0
        self._sat = 1.0
        self._marker_color = QColor(255, 0, 0)
        self._cache: QPixmap | None = None
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def sizeHint(self) -> QSize:
        return QSize(280, 280)

    def set_hue_sat(self, hue: float, sat: float, color: QColor | None = None) -> None:
        self._hue, self._sat = hue % 1.0, max(0.0, min(1.0, sat))
        if color is not None:
            self._marker_color = color
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._cache = None
        super().resizeEvent(event)

    def _geometry(self) -> tuple[QPointF, float]:
        side = min(self.width(), self.height(), self.MAX_DIAMETER) - 16
        return QPointF(self.width() / 2, self.height() / 2), side / 2

    def _render_wheel(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        center, radius = self._geometry()
        rect = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hue = QConicalGradient(center, 0)
        for i in range(13):
            hue.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0, 1.0, 1.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(hue)
        p.drawEllipse(rect)
        white = QRadialGradient(center, radius)
        white.setColorAt(0.0, QColor(255, 255, 255, 255))
        white.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(white)
        p.drawEllipse(rect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.drawEllipse(rect)
        p.end()
        return pm

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._cache is None:
            self._cache = self._render_wheel()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._cache)
        center, radius = self._geometry()
        angle = self._hue * 2 * math.pi
        marker = QPointF(
            center.x() + math.cos(angle) * self._sat * radius,
            center.y() - math.sin(angle) * self._sat * radius,
        )
        p.setPen(QPen(QColor(0, 0, 0, 90), 4))
        p.setBrush(self._marker_color)
        p.drawEllipse(marker, 10, 10)
        p.setPen(QPen(QColor(255, 255, 255), 2.5))
        p.drawEllipse(marker, 10, 10)

    def _pick(self, pos: QPointF) -> None:
        center, radius = self._geometry()
        dx, dy = pos.x() - center.x(), center.y() - pos.y()
        self._hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self._sat = min(1.0, math.hypot(dx, dy) / radius)
        self.update()
        self.hueSatChanged.emit(self._hue, self._sat)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pick(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position())


# --------------------------------------------------------------- color chips


class ColorChip(QAbstractButton):
    """A flat color sample; clicking opens the color dialog."""

    colorChanged = Signal(tuple)

    def __init__(self, color: Color, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color = color
        self.clicked.connect(self._choose)

    @property
    def color(self) -> Color:
        return self._color

    def set_color(self, color: Color) -> None:
        self._color = color
        self.setToolTip("#{:02X}{:02X}{:02X}".format(*color))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        border = _accent(self) if self.underMouse() else QColor(128, 128, 128, 110)
        p.setPen(QPen(border, 1.5 if self.underMouse() else 1))
        p.setBrush(to_qcolor(self._color))
        p.drawRoundedRect(rect, 7, 7)

    def enterEvent(self, event: object) -> None:
        self.update()

    def leaveEvent(self, event: object) -> None:
        self.update()

    def _choose(self) -> None:
        q = QColorDialog.getColor(to_qcolor(self._color), self, "Choose color")
        if q.isValid():
            self.set_color(from_qcolor(q))
            self.colorChanged.emit(self._color)


class Swatch(QAbstractButton):
    """Round preset color; shows a ring when it matches the current color."""

    picked = Signal(tuple)

    def __init__(self, color: Color, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._selected = False
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("#{:02X}{:02X}{:02X}".format(*color))
        self.clicked.connect(lambda: self.picked.emit(color))

    @property
    def color(self) -> Color:
        return self._color

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self._selected or self.underMouse():
            ring = _accent(self) if self._selected else QColor(160, 160, 160)
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect.adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(128, 128, 128, 90), 1))
        p.setBrush(to_qcolor(self._color))
        p.drawEllipse(rect.adjusted(5, 5, -5, -5))

    def enterEvent(self, event: object) -> None:
        self.update()

    def leaveEvent(self, event: object) -> None:
        self.update()


# -------------------------------------------------------------- form controls


class ToggleSwitch(QAbstractButton):
    """iOS/Fluent-style on/off switch with an optional label on the right."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text()) + 10 if self.text() else 0
        return QSize(40 + text_w, 24)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 3, 38, 20)
        on = self.isChecked()
        enabled = self.isEnabled()
        off_track = QColor(255, 255, 255, 45) if _is_dark(self) else QColor(0, 0, 0, 40)
        color = _accent(self) if on else off_track
        if not enabled:
            color.setAlpha(80)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(track, 10, 10)
        knob_x = track.right() - 18 if on else track.left() + 2
        p.setBrush(QColor(255, 255, 255) if enabled else QColor(200, 200, 200))
        p.drawEllipse(QRectF(knob_x, track.top() + 2, 16, 16))
        if self.text():
            p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            p.drawText(
                QRectF(48, 0, self.width() - 48, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )


class ValueSlider(QWidget):
    """Slider with its current value shown on the right (e.g. "75 %")."""

    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suffix = suffix
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.label = QLabel()
        self.label.setObjectName("Value")
        self.label.setMinimumWidth(44)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)
        self.slider.valueChanged.connect(self._on_change)
        self._on_change(self.slider.value())

    def _on_change(self, v: int) -> None:
        self.label.setText(f"{v}{self._suffix}")
        self.valueChanged.emit(v)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int) -> None:
        self.slider.setValue(v)


class NumberField(QLineEdit):
    """Compact numeric input: type a value, or use the mouse wheel / arrow keys to step it.

    Replaces QSpinBox, whose text area collapses when styled without arrow buttons.
    """

    valueChanged = Signal(int)

    def __init__(
        self, lo: int, hi: int, suffix: str = "", width: int = 64, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._lo, self._hi, self._suffix = lo, hi, suffix
        self._value = lo
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editingFinished.connect(self._commit)
        self._render()

    def value(self) -> int:
        return self._value

    def setValue(self, v: float) -> None:
        v = max(self._lo, min(self._hi, round(v)))
        changed = v != self._value
        self._value = v
        self._render()
        if changed:
            self.valueChanged.emit(v)

    def _render(self) -> None:
        self.setText(f"{self._value}{self._suffix}")

    def _commit(self) -> None:
        digits = "".join(ch for ch in self.text() if ch.isdigit() or ch == "-")
        try:
            self.setValue(int(digits))
        except ValueError:
            self._render()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.isEnabled():
            self.setValue(self._value + (1 if event.angleDelta().y() > 0 else -1))
            event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.setValue(self._value + (1 if event.key() == Qt.Key.Key_Up else -1))
        else:
            super().keyPressEvent(event)


# ------------------------------------------------------------------ previews


class TempGradient(_Rounded, QWidget):
    """Bar showing the color for every temperature, with a marker at the current reading."""

    LOW, HIGH = 20.0, 100.0
    PAD = 10  # keep end labels inside the widget

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stops: list[TempStop] = []
        self._blend = True
        self._now: float | None = None
        self._cache: QPixmap | None = None
        self._cache_key: tuple[object, ...] = ()
        self.setMinimumHeight(40)

    def set_data(self, stops: list[TempStop], blend: bool, now: float | None) -> None:
        if (stops, blend, now) == (self._stops, self._blend, self._now):
            return
        self._stops, self._blend, self._now = list(stops), blend, now
        self.update()

    def _bar(self) -> QRectF:
        return QRectF(self.PAD, 2, self.width() - 2 * self.PAD, self.height() - 20)

    def _x(self, temp: float) -> float:
        bar = self._bar()
        return bar.left() + (temp - self.LOW) / (self.HIGH - self.LOW) * bar.width()

    def _render_bar(self) -> QPixmap:
        bar = self._bar()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        r = min(self._radius, bar.height() / 2)
        clip.addRoundedRect(bar, r, r)
        p.setClipPath(clip)
        steps = max(int(bar.width()), 1)
        for i in range(steps):
            temp = self.LOW + i / steps * (self.HIGH - self.LOW)
            p.fillRect(
                QRectF(bar.left() + i, bar.top(), 1.5, bar.height()),
                to_qcolor(temperature_color(self._stops, temp, self._blend)),
            )
        p.setClipping(False)
        p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        for t in range(int(self.LOW), int(self.HIGH) + 1, 20):
            x = self._x(t)
            p.drawText(
                QRectF(x - 20, bar.bottom() + 2, 40, 16), Qt.AlignmentFlag.AlignCenter, f"{t}°"
            )
        p.end()
        return pm

    def paintEvent(self, event: QPaintEvent) -> None:
        text = self.palette().color(QPalette.ColorRole.PlaceholderText).rgba()
        key = (tuple(self._stops), self._blend, self.width(), self.height(), self._radius, text)
        if self._cache is None or key != self._cache_key:
            self._cache, self._cache_key = self._render_bar(), key
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._cache)
        if self._now is not None:
            bar = self._bar()
            x = self._x(max(self.LOW, min(self.HIGH, self._now)))
            p.setPen(QPen(QColor(0, 0, 0, 120), 4))
            p.drawLine(QPointF(x, bar.top() - 1), QPointF(x, bar.bottom() + 1))
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(x, bar.top() - 1), QPointF(x, bar.bottom() + 1))


class LedStrip(_Rounded, QWidget):
    """Live preview of what is being sent to the LEDs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._leds: list[Color] = []
        self.setFixedHeight(30)

    def set_leds(self, leds: list[Color]) -> None:
        if leds != self._leds:
            self._leds = leds
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = min(self._radius, rect.height() / 2)
        p.setPen(QPen(QColor(128, 128, 128, 70), 1))
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(rect, r, r)
        if not self._leds:
            return
        inner = rect.adjusted(8, 8, -8, -8)
        w = inner.width() / len(self._leds)
        p.setPen(Qt.PenStyle.NoPen)
        for i, c in enumerate(self._leds):
            p.setBrush(to_qcolor(c))
            p.drawRoundedRect(
                QRectF(inner.x() + i * w, inner.y(), max(w - 2, 1), inner.height()), 3, 3
            )
