"""A checkbox whose label wraps to the available width.

A plain ``QCheckBox`` reports its *full text width* as its minimum size, so a
long-label checkbox in a scroll-area sidebar pins the whole panel that wide —
the panel can never shrink to the viewport, so it clips and no neighbouring
word-wrap label ever gets to wrap. This composites a text-less ``QCheckBox``
with a word-wrapped ``QLabel`` (clicking the label toggles the box), so the
control's minimum width collapses to roughly one word and the label wraps to
whatever width the panel gives it.

Drop-in for the subset of the ``QCheckBox`` API the Analyze form uses:
``isChecked`` / ``setChecked`` / ``toggled`` / ``setEnabled`` / ``setToolTip`` /
``text`` / ``setText``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QWidget


class WrapCheckBox(QWidget):
    toggled = Signal(bool)

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._cb = QCheckBox(self)
        self._cb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lay.addWidget(self._cb, 0, Qt.AlignTop)
        self._lbl = QLabel(text, self)
        self._lbl.setWordWrap(True)
        # Let the label shrink below its text width (so the composite's minimum
        # width stays small) while still wrapping to the width it is given.
        self._lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self._lbl, 1)
        self._cb.toggled.connect(self.toggled)

    # clicking anywhere on the row (including the wrapped label) toggles
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._cb.toggle()
        super().mousePressEvent(event)

    # ── QCheckBox-compatible API ────────────────────────────────────────────
    def isChecked(self) -> bool:
        return self._cb.isChecked()

    def setChecked(self, value: bool) -> None:
        self._cb.setChecked(bool(value))

    def text(self) -> str:
        return self._lbl.text()

    def setText(self, text: str) -> None:
        self._lbl.setText(text)

    def setToolTip(self, tip: str) -> None:  # noqa: N802 (Qt casing)
        super().setToolTip(tip)
        self._cb.setToolTip(tip)
        self._lbl.setToolTip(tip)
