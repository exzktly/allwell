"""ResponsiveRow — panels side by side while the width holds them, stacked when
it does not.

A ``QHBoxLayout`` given less width than its children's minimums does not stop
laying them out: it overlaps and clips them. On the Statistics page that showed
as the comparison-group map with the group cards drawn *over* it, a section
title reading "COMP GROU", and a button cut off mid-word — a 1100x800 window
with the rail open and the pane split leaves each pane about 350px, against a
map floor of 220 and a card column that cannot go below its widest button.

The flip point is not a chosen number. It is the panels' own
``minimumSizeHint`` widths plus the spacing between them: the row stacks exactly
when side by side would start clipping, and unstacks when it would not. A number
would have to be re-derived every time a panel's contents changed, and the
number that matters is one the panels already know.

Hysteresis, because the two orientations have different heights and therefore
different scrollbar states, and a width sitting on the flip point would
otherwise oscillate. :data:`_HYSTERESIS` is the margin a stacked row must gain
back before it returns to a single line.

``features/batch_export/base_panel.py`` grew this behaviour first, wired into
that panel's own class and reading the width of the scroll-area viewport it sits
in rather than its own. This is the general form of the same rule; that panel is
not converted here because its version also drives a separator's orientation and
its own width caps.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QWidget

#: How much width a stacked row must regain before it goes back to one line.
#: Not a design token: it is a margin around a comparison, and its only job is to
#: be wider than the width a scrollbar appearing or disappearing adds or removes.
_HYSTERESIS = 60


def _effective_min_width(widget: QWidget) -> int:
    """How narrow *widget* can actually be drawn.

    Both halves are needed and neither is enough alone. ``minimumSizeHint`` is
    what a widget derives from its own contents and ignores a minimum set on it
    from outside; ``minimumWidth`` is that explicit floor and is 0 on a widget
    that never had one. Reading only the hint would have missed the Statistics
    map's ``setMinimumWidth(PLATE_MIN_WIDTH)`` — the floor the stacking exists
    to protect — and the row would have gone on clipping exactly where it did
    before. Qt itself takes the larger of the two, so this does too.
    """
    return max(widget.minimumWidth(), max(0, widget.minimumSizeHint().width()))


class ResponsiveRow(QWidget):
    """A row of panels that becomes a column when the panels no longer fit.

    Add panels with :meth:`add_panel` rather than ``addWidget``: each one
    carries the stretch it takes while the row is horizontal and the alignment
    it takes in each orientation, and the row needs all of that to flip cleanly.
    """

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 0) -> None:
        super().__init__(parent)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        self._panels: list[tuple[QWidget, int, object, object]] = []

    # ── building ────────────────────────────────────────────────────────────

    def add_panel(self, widget: QWidget, *, stretch: int = 0,
                  row_alignment=None, column_alignment=None) -> None:
        """Append *widget*.

        *stretch* applies only while the row is horizontal — stacked, a panel
        that grabbed the horizontal slack would also grab the vertical, and the
        page below it would lose the room the row was meant to leave.
        """
        self._panels.append((widget, stretch, row_alignment, column_alignment))
        self._box.addWidget(widget)
        self._relayout(stacked=self.is_stacked())

    # ── state ───────────────────────────────────────────────────────────────

    def is_stacked(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.TopToBottom

    def needed_width(self) -> int:
        """The width one line of these panels needs before it starts clipping."""
        if not self._panels:
            return 0
        widths = sum(_effective_min_width(w) for w, _s, _r, _c in self._panels)
        return widths + self._box.spacing() * (len(self._panels) - 1)

    # ── flipping ────────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # Qt override
        super().resizeEvent(event)
        self.apply_orientation()

    def apply_orientation(self, avail: int | None = None) -> None:
        """Stack or unstack for *avail* (default: the width actually available).

        The row's own width is not enough on its own. Pinned to a side-by-side
        minimum wider than its parent, a widget reports that minimum rather than
        the space it has, and the flip that would relieve it would never fire —
        so the parent's width is taken as a second opinion and the smaller wins.
        """
        if not self._panels:
            return
        if avail is None:
            avail = self.width()
            parent = self.parentWidget()
            if parent is not None:
                avail = min(avail, parent.width())
        need = self.needed_width()
        stacked_now = self.is_stacked()
        if not stacked_now and avail < need:
            stack = True
        elif stacked_now and avail > need + _HYSTERESIS:
            stack = False
        else:
            return
        self._box.setDirection(QBoxLayout.Direction.TopToBottom if stack
                               else QBoxLayout.Direction.LeftToRight)
        self._relayout(stacked=stack)

    def _relayout(self, *, stacked: bool) -> None:
        for i, (widget, stretch, row_align, col_align) in enumerate(self._panels):
            self._box.setStretch(i, 0 if stacked else stretch)
            align = col_align if stacked else row_align
            self._box.setAlignment(widget, align or Qt.Alignment())
