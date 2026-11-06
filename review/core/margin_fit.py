"""Margins for a figure whose axes a divider places.

Constrained layout sizes a figure's margins from the text hanging off its
axes, but it cannot place an axes that an ``axes_grid1`` divider positions: the
divider reads the axes' *original* position and ignores the engine's. The Heat
Map is built that way (its colour bar is a persistent axes beside the grid), so
on a phone its colour bar's tick labels and label were drawn in the six percent
margin the renderer left, and ran off the edge of the figure; held sideways, its
column ticks and title ran off the top.

:func:`inset_for_overhang` does the one thing the engine would have: it moves
the axes in from whichever side text hangs off the figure, by as much as it
hangs (the span the axes occupy is mapped into what is left of it, so a plot and
its colour bar keep their arrangement). Measured as drawn, so it is a function
of the figure's own size and text and knows nothing about the layout it is in.
"""

from __future__ import annotations

from matplotlib.transforms import Bbox

#: On an axes: the position it had before this module moved it, so a second
#: fit starts from the renderer's margins rather than from the first fit's.
_HOME = "_aw_margin_home"

#: Breathing room, as a share of the figure's size, kept between the text and
#: the figure's edge.
_PAD = 0.01

#: How many times the overhang is measured again after the axes have moved.
#: Moving an axes changes its tick labels' own width a little (a divider's bar
#: is a share of the grid, and a label is placed from its tick labels), so one
#: measurement leaves a few pixels hanging.
_PASSES = 3


def _overhang(fig, renderer) -> tuple[float, float, float, float]:
    """``(left, right, bottom, top)`` pixels of any axes' drawn text past the figure."""
    left = right = bottom = top = 0.0
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        box = ax.get_tightbbox(renderer)
        if box is None:
            continue
        left = max(left, -box.x0)
        right = max(right, box.x1 - fig.bbox.width)
        bottom = max(bottom, -box.y0)
        top = max(top, box.y1 - fig.bbox.height)
    return left, right, bottom, top


def divider_placed(fig) -> bool:
    """Whether any axes of *fig* is placed by an ``axes_grid1`` divider.

    A colour bar also carries an axes locator, but matplotlib's own, which
    constrained layout does handle: only a divider's locator is the case here.
    """
    return any(loc is not None and type(loc).__module__ != "matplotlib.colorbar"
               for loc in (ax.get_axes_locator() for ax in fig.axes))


def restore(fig) -> None:
    """Give every axes this module moved its own position back."""
    for ax in fig.axes:
        home = getattr(ax, _HOME, None)
        if home is not None:
            ax.set_position(home)
            setattr(ax, _HOME, None)


def _map(lo: float, hi: float, v: float, d0: float, d1: float) -> float | None:
    scale = (hi - lo - d0 - d1) / (hi - lo) if hi > lo else 0.0
    return None if scale <= 0.05 else lo + d0 + (v - lo) * scale


def _apply(placed, home, spans, margins) -> bool:
    """Map each span of the *home* positions into what is left of it after
    *margins* (``dl, dr, db, dt``, shares of the figure); ``False`` when nothing
    would be left."""
    (xlo, xhi), (ylo, yhi) = spans
    dl, dr, db, dt = margins
    boxes = []
    for pos in home:
        x0, x1 = (_map(xlo, xhi, v, dl, dr) for v in (pos.x0, pos.x1))
        y0, y1 = (_map(ylo, yhi, v, db, dt) for v in (pos.y0, pos.y1))
        if None in (x0, x1, y0, y1):
            return False
        boxes.append(Bbox.from_extents(x0, y0, x1, y1))
    for ax, box in zip(placed, boxes):
        # The span the axes occupy is mapped into what is left of it, so axes
        # that sit side by side (a plot and its colour bar) keep their
        # arrangement and the outermost edge moves by exactly the overhang.
        ax.set_position(box)
    return True


def inset_for_overhang(fig) -> None:
    """Move every in-grid axes in from the sides that text hangs off the figure."""
    restore(fig)
    renderer = fig.canvas.get_renderer()
    width, height = fig.bbox.width, fig.bbox.height
    placed = [ax for ax in fig.axes if ax.get_subplotspec() is not None]
    if width <= 0 or height <= 0 or not placed:
        return                             # a divider's axes follow their parent
    home = [ax.get_position(original=True).frozen() for ax in placed]
    spans = ((min(p.x0 for p in home), max(p.x1 for p in home)),
             (min(p.y0 for p in home), max(p.y1 for p in home)))
    margins = [0.0, 0.0, 0.0, 0.0]
    for _ in range(_PASSES):
        over = _overhang(fig, renderer)
        if not any(o > 0 for o in over):
            break
        for i, (o, size) in enumerate(zip(over, (width, width, height, height))):
            if o > 0:
                margins[i] += (o + _PAD * size) / size
        if not _apply(placed, home, spans, margins):
            _apply(placed, home, spans, (0.0, 0.0, 0.0, 0.0))
            break
        for ax, pos in zip(placed, home):
            setattr(ax, _HOME, pos)
