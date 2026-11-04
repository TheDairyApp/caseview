"""Case display pages editor: one card per page (preview, name, on/off, three readouts),
add/remove/reorder, seconds per page. Saves to config.toml and applies live."""

from __future__ import annotations

import copy
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from caseview.core.config import SLOT_IDS, Config, DisplayPage, default_pages
from caseview.core.metrics import DISPLAY_SOURCES
from caseview.ui.display_preview import READOUTS, CaseDisplayPreview
from caseview.ui.theme import Theme, apply_theme
from caseview.ui.widgets import ToggleSwitch, ValueSlider

READOUT_LABEL = {slot: f"{label} readout ({digits} digits)" for slot, digits, label in READOUTS}


class PageCard(QFrame):
    changed = Signal()
    moveRequested = Signal(object, int)
    removeRequested = Signal(object)

    def __init__(self, page: DisplayPage) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.name = QLineEdit(page.name)
        self.name.setMaxLength(24)
        self.name.setFixedWidth(160)
        self.name.textChanged.connect(self._edited)
        head.addWidget(self.name)
        self.enabled = ToggleSwitch("Show this page")
        self.enabled.setChecked(page.enabled)
        self.enabled.toggled.connect(self._edited)
        head.addWidget(self.enabled)
        head.addStretch()
        for text, tip, action in (
            ("↑", "Move up", lambda: self.moveRequested.emit(self, -1)),
            ("↓", "Move down", lambda: self.moveRequested.emit(self, 1)),
            ("✕", "Remove page", lambda: self.removeRequested.emit(self)),
        ):
            b = QPushButton(text)
            b.setObjectName("Icon")
            b.setToolTip(tip)
            b.clicked.connect(action)
            head.addWidget(b)
        layout.addLayout(head)

        self.preview = CaseDisplayPreview(compact=True)
        layout.addWidget(self.preview)

        self.boxes: dict[str, QComboBox] = {}
        for slot in SLOT_IDS:
            row = QHBoxLayout()
            label = QLabel(READOUT_LABEL[slot])
            label.setObjectName("Muted")
            label.setFixedWidth(150)
            row.addWidget(label)
            box = QComboBox()
            for key, text in DISPLAY_SOURCES.items():
                box.addItem(text, key)
            box.setCurrentIndex(max(box.findData(getattr(page, slot)), 0))
            box.currentIndexChanged.connect(self._edited)
            row.addWidget(box, 1)
            layout.addLayout(row)
            self.boxes[slot] = box

    def _edited(self) -> None:
        self.page.name = self.name.text().strip() or "Page"
        self.page.enabled = self.enabled.isChecked()
        for slot, box in self.boxes.items():
            setattr(self.page, slot, str(box.currentData()))
        self.changed.emit()

    def refresh(self, values: dict[str, float]) -> None:
        sources = self.page.sources()
        self.preview.set_page(
            self.page.name, sources, {s: values.get(src) for s, src in sources.items()}
        )


class DisplayWindow(QWidget):
    """Edits a copy of the pages. `apply(pages, seconds, persist)` pushes them to the
    running engine (live preview on the case) and, with persist=True, saves them.
    Closing without Save restores the pages the window opened with."""

    def __init__(
        self,
        config: Config,
        values: Callable[[], dict[str, float]],
        current_page: Callable[[], str],
        apply: Callable[[list[DisplayPage], float, bool], None],
        theme: Theme,
    ) -> None:
        super().__init__()
        self.setWindowTitle("CaseView – Case display")
        self.setMinimumSize(560, 520)
        self.resize(640, 820)
        self._values, self._current, self._apply = values, current_page, apply
        self.pages = copy.deepcopy(config.pages)
        self.seconds = config.page_seconds
        self._original = (copy.deepcopy(config.pages), config.page_seconds)
        self._saved = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        title = QLabel("Case display")
        title.setObjectName("Title")
        root.addWidget(title)
        hint = QLabel(
            "The display rotates through the pages that are switched on. Readouts are named "
            "after the labels printed on the case; the 2-digit ones show up to 99."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.live = CaseDisplayPreview()
        root.addWidget(self.live)

        row = QHBoxLayout()
        label = QLabel("Seconds per page")
        label.setObjectName("Muted")
        row.addWidget(label)
        self.seconds_slider = ValueSlider(2, 30, " s")
        self.seconds_slider.setValue(int(self.seconds))
        self.seconds_slider.valueChanged.connect(self._seconds_changed)
        row.addWidget(self.seconds_slider, 1)
        root.addLayout(row)

        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(12)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 6, 0)
        holder_layout.addLayout(self.cards_box)
        holder_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        add = QPushButton("+  Add page")
        add.setObjectName("Ghost")
        add.clicked.connect(self._add)
        reset = QPushButton("Reset to defaults")
        reset.setObjectName("Ghost")
        reset.clicked.connect(self._reset)
        foot.addWidget(add)
        foot.addWidget(reset)
        foot.addStretch()
        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        foot.addWidget(save)
        root.addLayout(foot)

        self.cards: list[PageCard] = []
        self._rebuild()
        apply_theme(self, theme)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def _rebuild(self) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        for page in self.pages:
            card = PageCard(page)
            card.changed.connect(self._changed)
            card.moveRequested.connect(self._move)
            card.removeRequested.connect(self._remove)
            self.cards_box.addWidget(card)
            self.cards.append(card)
        self.refresh()

    def _changed(self) -> None:
        self._apply(copy.deepcopy(self.pages), self.seconds, False)  # live on the case

    def _seconds_changed(self, v: int) -> None:
        self.seconds = float(v)
        self._changed()

    def _move(self, card: PageCard, delta: int) -> None:
        i = self.pages.index(card.page)
        j = max(0, min(len(self.pages) - 1, i + delta))
        if i != j:
            self.pages.insert(j, self.pages.pop(i))
            self._rebuild()
            self._changed()

    def _remove(self, card: PageCard) -> None:
        if len(self.pages) > 1:
            self.pages.remove(card.page)
            self._rebuild()
            self._changed()

    def _add(self) -> None:
        self.pages.append(
            DisplayPage(f"Page {len(self.pages) + 1}", "cpu_temp", "cpu_fan", "gpu_temp")
        )
        self._rebuild()
        self._changed()

    def _reset(self) -> None:
        self.pages = default_pages()
        self.seconds = 5.0
        self.seconds_slider.setValue(5)
        self._rebuild()
        self._changed()

    def _save(self) -> None:
        self._apply(copy.deepcopy(self.pages), self.seconds, True)
        self._saved = True
        self.close()

    def refresh(self) -> None:
        values = self._values()
        current = self._current()
        page = next((p for p in self.pages if p.name == current), None) or self.pages[0]
        sources = page.sources()
        self.live.set_page(
            f"Now showing: {page.name}", sources, {s: values.get(src) for s, src in sources.items()}
        )
        for card in self.cards:
            card.refresh(values)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        if not self._saved:
            self._apply(copy.deepcopy(self._original[0]), self._original[1], False)
        event.accept()
