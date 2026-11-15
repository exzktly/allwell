"""The Statistics comparison-group card's count label, which elides rather than
holding the card wider than its column.

A card is a dot, the group's name, a muted count ("(2 solo wells)") and the
rename and delete buttons. The group list's width floor is one card wide, capped
at the plate's own floor (``controller._stats_floor_group_list``) so a long card
cannot push the map out of the row; a card wider than the cap is meant to give
the difference back from its own text. A ``QLabel`` cannot do that — its minimum
width is its whole text — so in phone layout, where the two buttons are
touch-sized, a card on an unfolded foldable held upright needed 324 px in a
305 px list: the list scrolled sideways, and the scroll bar took 19 px of a list
already two cards tall, cutting the second card short. The count is the part
that gives, because it is the one that is repeated in full in the tooltip and
the map beside it shows the same wells.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidingLabel(QLabel):
    """A one-line label that may be narrower than its text, and then draws the
    text cut with "…" and carries it whole in its tooltip.

    Its size hint is the whole text, so it takes the whole text whenever the
    layout has the room; its minimum is one character and the ellipsis. The
    elided string is what ``text()`` returns while it is cut, so whatever reads
    the label reads what is drawn.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setToolTip(text)
        super().setText(text)

    def full_text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:  # Qt override
        hint = super().sizeHint()
        fm = self.fontMetrics()
        m = self.contentsMargins()
        return QSize(fm.horizontalAdvance(self._full) + m.left() + m.right() + 2 * self.margin(),
                     hint.height())

    def minimumSizeHint(self) -> QSize:  # Qt override
        fm = self.fontMetrics()
        m = self.contentsMargins()
        return QSize(fm.horizontalAdvance(self._full[:1] + "…") + m.left() + m.right() + 2 * self.margin(),
                     super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # Qt override
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:  # Qt override
        super().changeEvent(event)
        # A restyle changes the font, and with it what fits.
        if event.type() in (event.Type.FontChange, event.Type.StyleChange):
            self._elide()
            self.updateGeometry()

    def _elide(self) -> None:
        room = self.contentsRect().width() - 2 * self.margin()
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, room))
        if shown != super().text():
            super().setText(shown)
