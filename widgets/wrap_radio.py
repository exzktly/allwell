"""A radio button whose label wraps to the available width.

A plain ``QRadioButton`` reports its *full text width* as its minimum size, so a
long-label radio inside a scroll area pins the whole page that wide: the page
can never shrink to a narrow viewport, and because a horizontal scroll bar is
off there the right-hand part of the page is simply out of reach.

:class:`WrapRadioRow` pairs a text-less ``QRadioButton`` with a word-wrapped
``QLabel`` (clicking the label selects the button), so the row's minimum width
collapses to roughly one word and the label wraps to whatever width it is
given. The button itself stays a real ``QRadioButton`` — ``row.radio`` — so it
joins a ``QButtonGroup`` and emits ``toggled`` exactly as a plain one would.
Same idea as :class:`widgets.wrap_checkbox.WrapCheckBox`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QRadioButton, QSizePolicy, QWidget

from ui import theme


class _ClickThroughLabel(QLabel):
    """A wrapped label that presses the radio button beside it."""

    def __init__(self, text: str, radio: QRadioButton, parent: QWidget) -> None:
        super().__init__(text, parent)
        self._radio = radio
        self.setWordWrap(True)
        # Preferred, not Ignored: Ignored discards the label's preferred width
        # and the text collapses to nothing in a horizontal row.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def mousePressEvent(self, event) -> None:  # Qt naming
        if event.button() == Qt.LeftButton and self._radio.isEnabled():
            self._radio.click()
        super().mousePressEvent(event)


class WrapRadioRow(QWidget):
    """``[radio] wrapped label`` as one row; ``.radio`` is the real button."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.Spacing.sm)
        self.radio = QRadioButton(self)
        self.radio.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # A text-less radio still reserves the indicator↔label gap; zero it so
        # the layout spacing above is the only separation (see WrapCheckBox).
        self.radio.setStyleSheet(
            "QRadioButton { spacing: 0px; }"
            "QRadioButton::indicator { margin: 0px; }"
        )
        self.radio.setAccessibleName(text)
        lay.addWidget(self.radio, 0, Qt.AlignTop)
        self._lbl = _ClickThroughLabel(text, self.radio, self)
        lay.addWidget(self._lbl, 1)
        # The label wraps, so the row's height depends on its width; a QWidget
        # does not inherit that from its child (see WrapCheckBox).
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def text(self) -> str:
        return self._lbl.text()

    def hasHeightForWidth(self) -> bool:  # Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # Qt naming
        """Height this row needs at *width*, never less than the indicator."""
        lay = self.layout()
        margins = lay.contentsMargins()
        inner = (width - self.radio.sizeHint().width() - lay.spacing()
                 - margins.left() - margins.right())
        label_h = self._lbl.heightForWidth(max(0, inner))
        return (max(self.radio.sizeHint().height(), label_h)
                + margins.top() + margins.bottom())

    def setToolTip(self, tip: str) -> None:  # Qt casing
        super().setToolTip(tip)
        self.radio.setToolTip(tip)
        self._lbl.setToolTip(tip)
