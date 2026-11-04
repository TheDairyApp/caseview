"""Painted mock-up of the case display: three LED readouts, as the hardware shows them."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from caseview.core.metrics import DISPLAY_SOURCES

LED = QColor(80, 220, 255)
LED_OFF = QColor(80, 220, 255, 28)

# (readout id, digits, label printed on the case)
READOUTS = [("cpu_temp", 2, "CPU"), ("cpu_fan", 4, "FAN"), ("gpu_temp", 2, "GPU")]


def shown_text(value: float | None, digits: int) -> str:
    """The digits the case would light up for `value` (blank when unknown)."""
    if value is None:
        return ""
    top = 10**digits - 1
    return str(max(0, min(top, round(value))))


class CaseDisplayPreview(QWidget):
    def __init__(self, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact = compact
        self._values: dict[str, float | None] = {}
        self._sources: dict[str, str] = {}
        self._title = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(74 if compact else 104)

    def sizeHint(self) -> QSize:
        return QSize(360, self.height())

    def set_page(
        self, title: str, sources: dict[str, str], values: dict[str, float | None]
    ) -> None:
        if (title, sources, values) != (self._title, self._sources, self._values):
            self._title, self._sources, self._values = title, dict(sources), dict(values)
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(QColor(8, 10, 14))
        p.drawRoundedRect(rect, 12, 12)

        digit_font = QFont("Consolas")
        digit_font.setStyleHint(QFont.StyleHint.Monospace)
        digit_font.setBold(True)
        digit_font.setPointSizeF(20 if self._compact else 28)
        small = QFont(self.font())
        small.setPointSizeF(7.5)
        fm = QFontMetricsF(digit_font)
        char_w = fm.horizontalAdvance("8")

        widths = [d * char_w + 24 for _, d, _ in READOUTS]
        gap = (rect.width() - sum(widths)) / (len(READOUTS) + 1)
        x = rect.left() + gap
        top = rect.top() + (10 if self._compact else 14)
        digit_h = fm.height()
        for (slot, digits, case_label), w in zip(READOUTS, widths, strict=True):
            cell = QRectF(x, top, w, digit_h)
            text = shown_text(self._values.get(slot), digits)
            p.setFont(digit_font)
            p.setPen(LED_OFF)
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, "8" * digits)  # unlit segments
            p.setPen(LED)
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, text.rjust(digits))
            p.setFont(small)
            p.setPen(QColor(150, 152, 160))
            source = DISPLAY_SOURCES.get(self._sources.get(slot, ""), "")
            caption = (
                case_label
                if self._compact
                else f"{case_label} · {source}"
                if source
                else case_label
            )
            p.drawText(
                QRectF(x - gap / 2, cell.bottom() + 2, w + gap, 16),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                p.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, int(w + gap)),
            )
            x += w + gap
        if self._title and not self._compact:
            p.setFont(small)
            p.setPen(QColor(150, 152, 160))
            p.drawText(
                rect.adjusted(12, 6, -12, -6),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                self._title,
            )
