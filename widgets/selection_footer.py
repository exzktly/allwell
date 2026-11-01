"""SelectionFooter — the line under a well picker: how many, **All**, **None**.

Every multi-select well picker on the rail ends in the same three things, in
the same words, so a selection is read and changed the same way on every page:
a count on the left ("3 wells selected"), then **All** and **None** on the
right. The host decides what the count says and what the two buttons do — in
group mode a plate selects sets rather than wells, so the count reads
"2/3 sets visible" and **All** shows every set — and this widget only lays the
row out and forwards the clicks.

``setText`` sets the count, so a host that already writes a count into a
``QLabel`` can hand it this widget instead. ``setAllVisible(False)`` hides
**All** for a picker that is single-select at the moment, where "all" has no
meaning.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ui import theme


class SelectionFooter(QWidget):
    #: The user pressed **All**.
    selectAllRequested = Signal()
    #: The user pressed **None**.
    selectNoneRequested = Signal()

    def __init__(self, parent: QWidget | None = None, *,
                 all_tooltip: str = "Select every well on the plate.",
                 none_tooltip: str = "Clear the selection.") -> None:
        super().__init__(parent)
        self.setObjectName("SelectionFooter")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, theme.Spacing.xs, 0, theme.Spacing.xs)
        lay.setSpacing(theme.Spacing.sm)

        self._count = QLabel("No wells selected", self)
        self._count.setObjectName("Muted")
        self._count.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._count.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self._count, 1)

        self._all = QPushButton("All", self)
        self._all.setProperty("variant", "secondary")
        self._all.setToolTip(all_tooltip)
        self._all.clicked.connect(lambda _=False: self.selectAllRequested.emit())
        lay.addWidget(self._all)

        self._none = QPushButton("None", self)
        self._none.setProperty("variant", "secondary")
        self._none.setToolTip(none_tooltip)
        self._none.clicked.connect(lambda _=False: self.selectNoneRequested.emit())
        lay.addWidget(self._none)

    # ── API ──────────────────────────────────────────────────────────────
    def setText(self, text: str) -> None:
        """Set the count shown on the left."""
        self._count.setText(text)

    def text(self) -> str:
        return self._count.text()

    def setAllVisible(self, on: bool) -> None:
        self._all.setVisible(bool(on))

    def allButton(self) -> QPushButton:
        return self._all

    def noneButton(self) -> QPushButton:
        return self._none
