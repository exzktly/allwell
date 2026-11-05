"""A label that stays on one line while it fits and wraps only when squeezed.

A word-wrapped ``QLabel`` reports a *narrow* preferred width (it assumes it will
wrap), so beside other columns it is given less room than its text needs even
when the page has plenty — ``DAPI Channel:`` broke onto two lines on a wide
desktop. A plain label does the opposite: its minimum width is its whole text,
so a row of them holds a narrow page wider than its viewport.

:class:`SqueezeWrapLabel` is the two together: its preferred width is its text on
one line, and its minimum width is its longest word, with word wrap on in
between. A grid column of them is as wide as the longest text where there is
room and gives way, wrapping, where there is not.

No All-Well coupling — a plain reusable widget (belongs in ``widgets/``).
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel, QWidget


class SqueezeWrapLabel(QLabel):
    """One line at its preferred width; wraps at its words when given less."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)

    def sizeHint(self) -> QSize:  # Qt naming
        """The text on one line (its widest line, if it holds line breaks)."""
        metrics = self.fontMetrics()
        lines = self.text().split("\n")
        margins = self.contentsMargins()
        width = (max(metrics.horizontalAdvance(line) for line in lines)
                 + margins.left() + margins.right() + 2 * self.margin() + max(0, self.indent()))
        height = (metrics.lineSpacing() * len(lines) + margins.top() + margins.bottom()
                  + 2 * self.margin())
        return QSize(width, height)
