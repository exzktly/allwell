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
        # A text-less checkbox still reserves the indicator↔label ``spacing`` gap,
        # leaving dead space between the box and this composite's own wrapped
        # label. Zero it so the label sits right after the indicator (the 6px gap
        # below is the only separation).
        self._cb.setStyleSheet("QCheckBox { spacing: 0px; }")
        lay.addWidget(self._cb, 0, Qt.AlignTop)
        self._lbl = QLabel(text, self)
        self._lbl.setWordWrap(True)
        # A word-wrapped label already reports a small minimum width (its widest
        # word), so the composite never pins a narrow sidebar — but use Preferred,
        # NOT Ignored: Ignored discards the label's preferred width, so in a
        # horizontal row without an explicit stretch the label collapsed to 0 px
        # (no text) and the wrapped height ballooned. Preferred keeps a sane
        # preferred width while still wrapping to whatever width it's given.
        self._lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        lay.addWidget(self._lbl, 1)
        self._cb.toggled.connect(self.toggled)
        # The inner label wraps, so this composite's height depends on the width
        # it is given — but a QWidget does not inherit its child's
        # ``heightForWidth``, and the default size policy does not advertise one.
        # Without this a parent column sizes the row from a single line's height
        # and a label that wraps to two spills over whatever sits below it.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # ── height depends on width (the label wraps) ────────────────────────────
    def hasHeightForWidth(self) -> bool:  # Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # Qt naming
        """Height this row needs at *width*, never less than the indicator."""
        lay = self.layout()
        margins = lay.contentsMargins()
        inner = (width - self._cb.sizeHint().width() - lay.spacing()
                 - margins.left() - margins.right())
        label_h = self._lbl.heightForWidth(max(0, inner))
        return (max(self._cb.sizeHint().height(), label_h)
                + margins.top() + margins.bottom())

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

    def setToolTip(self, tip: str) -> None:  # Qt casing
        super().setToolTip(tip)
        self._cb.setToolTip(tip)
        self._lbl.setToolTip(tip)
