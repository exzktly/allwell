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
that panel's own class. This is the general form of the same rule. That panel is
not converted here — its version also drives a separator's orientation and its
own width caps, and the conversion is a change to a page this one does not touch
— but the two now disagree on the rule itself, which is worth naming rather than
leaving for someone to find: `base_panel` measures with ``minimumSizeHint``
alone plus a fixed allowance, so a panel given an explicit ``setMinimumWidth``
is measured as narrower than it can be drawn, and it repeats
:data:`_HYSTERESIS`'s value as a bare literal. Whichever is converted first
should take this module's :func:`_effective_min_width`.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractScrollArea, QBoxLayout, QScrollArea, QWidget

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
        # Both, and in this order. ``_relayout`` is what gives the new panel its
        # stretch and alignment, and ``apply_orientation`` returns without doing
        # anything when the orientation is already right — so calling only the
        # latter left every panel added to a row that did not need to flip with
        # no stretch at all. Then re-decide rather than inherit: a row already
        # sized and handed another panel needs more width than the decision on
        # file was made with, and nothing else will ask again until the next
        # resize, which may never come.
        self._relayout(stacked=self.is_stacked())
        self.apply_orientation()

    # ── state ───────────────────────────────────────────────────────────────

    def is_stacked(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.TopToBottom

    def needed_width(self) -> int:
        """The width one line of these panels needs before it starts clipping.

        Hidden panels are not counted, nor are the gaps beside them: a layout
        does not reserve width for a widget it is not drawing, so charging for
        one would stack a row that has room to spare — and a host that shows and
        hides a panel is exactly the caller most likely to be near the boundary.
        """
        shown = [w for w, _s, _r, _c in self._panels if not w.isHidden()]
        if not shown:
            return 0
        widths = sum(_effective_min_width(w) for w in shown)
        return widths + self._box.spacing() * (len(shown) - 1)

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
            avail = self._available_width()
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

    def _available_width(self) -> int:
        """The width this row actually has to lay panels out in.

        Inside a vertically-scrolling host with horizontal scrolling off, that
        is the **viewport's** width and not the row's own: the scroll area sizes
        its inner widget to at least that widget's minimum, so once the row is
        too wide the whole page is too wide and every width up the chain repeats
        the same inflated number. ``ResponsiveScroll`` also pushes this value in
        on its own resize, because a row already pinned at its minimum may not
        receive a resize of its own to react to.

        Outside one, the row's own width is the answer. An earlier version also
        took ``min(width, parent.width())``, on the reasoning that a row pinned
        wider than its parent reports what it wants rather than what it has —
        but with the viewport read above and ``add_panel`` re-deciding as panels
        arrive, no arrangement was found that reaches that state, and a branch
        no test can enter is not worth the sentence explaining it.
        """
        node = self.parentWidget()
        while isinstance(node, QWidget):
            if isinstance(node, QAbstractScrollArea):
                return node.viewport().width()
            node = node.parentWidget()
        return self.width()

    def _relayout(self, *, stacked: bool) -> None:
        # ``i`` is both the panel's index and its layout item's, which holds only
        # while ``add_panel`` is the only thing that puts items in the box. A
        # caller reaching past it — a stretch, a separator — would silently give
        # every later panel its neighbour's stretch and alignment, which looks
        # like a layout bug and reads like a correct loop.
        if self._box.count() != len(self._panels):
            raise RuntimeError(
                "ResponsiveRow's layout holds "
                f"{self._box.count()} items for {len(self._panels)} panels: "
                "add every child through add_panel()")
        for i, (widget, stretch, row_align, col_align) in enumerate(self._panels):
            self._box.setStretch(i, 0 if stacked else stretch)
            align = col_align if stacked else row_align
            self._box.setAlignment(widget, align or Qt.Alignment())


class ResponsiveScroll(QScrollArea):
    """A vertically-scrolling host that keeps the rows inside it told how much
    width they really have.

    Horizontal scrolling is off, so the page can never be dragged sideways and
    the rows must fit the viewport — which is the whole reason they flip. A row
    pinned at its own side-by-side minimum does not necessarily receive a resize
    event when the viewport shrinks under it, so the push has to come from here
    rather than being left to each row to notice.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def resizeEvent(self, event) -> None:  # Qt override
        super().resizeEvent(event)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        """Re-evaluate every row inside against the current viewport width."""
        host = self.widget()
        if host is None:
            return
        avail = self.viewport().width()
        for row in host.findChildren(ResponsiveRow):
            row.apply_orientation(avail)
