"""Side margins for a figure whose axes a divider places.

Constrained layout sizes a figure's margins from the text hanging off its
axes, but it cannot place an axes that an ``axes_grid1`` divider positions: the
divider reads the axes' *original* position and ignores the engine's. The Heat
Map is built that way (its colour bar is a persistent axes beside the grid), so
on a phone its colour bar's tick labels and label were drawn in the six percent
margin the renderer left, and ran off the edge of the figure.

:func:`inset_for_overhang` does the one thing the engine would have: it moves
the axes in from whichever side text hangs off the figure, by as much as it
hangs (the span the axes occupy is mapped into what is left of it, so a plot and its
colour bar keep their arrangement). Measured as drawn, so it is a function of
the figure's own size and text and knows nothing about the layout it is in.
"""

from __future__ import annotations

from matplotlib.transforms import Bbox

#: On an axes: the position it had before this module moved it, so a second
#: fit starts from the renderer's margins rather than from the first fit's.
_HOME = "_aw_margin_home"

#: Breathing room, as a share of the figure's width, kept between the text and
#: the figure's edge.
_PAD = 0.01


def _tight_extents(fig, renderer) -> tuple[float, float]:
    """``(left, right)`` pixel overhang of any axes' drawn text past the figure."""
    left = right = 0.0
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        box = ax.get_tightbbox(renderer)
        if box is None:
            continue
        left = max(left, -box.x0)
        right = max(right, box.x1 - fig.bbox.width)
    return left, right


def restore(fig) -> None:
    """Give every axes this module moved its own position back."""
    for ax in fig.axes:
        home = getattr(ax, _HOME, None)
        if home is not None:
            ax.set_position(home)
            setattr(ax, _HOME, None)


#: How many times the overhang is measured again after the axes have moved.
#: Moving an axes changes its tick labels' own width a little (a divider's bar
#: is a share of the grid, and a label is placed from its tick labels), so one
#: measurement leaves a few pixels hanging.
_PASSES = 3


def _apply(placed, home, lo, hi, dl, dr) -> bool:
    """Map the span *lo*..*hi* of the *home* positions into what is left after
    the margins *dl* and *dr*; ``False`` when nothing would be left."""
    scale = (hi - lo - dl - dr) / (hi - lo) if hi > lo else 0.0
    if scale <= 0.05:
        return False
    for ax, pos in zip(placed, home):
        # The span the axes occupy is mapped into what is left of it, so axes
        # that sit side by side (a plot and its colour bar) keep their
        # arrangement and the outermost edge moves by exactly the overhang.
        ax.set_position(Bbox.from_extents(lo + dl + (pos.x0 - lo) * scale, pos.y0,
                                          lo + dl + (pos.x1 - lo) * scale, pos.y1))
    return True


def inset_for_overhang(fig) -> None:
    """Move every in-grid axes in from the side that text hangs off the figure."""
    restore(fig)
    renderer = fig.canvas.get_renderer()
    width = fig.bbox.width
    placed = [ax for ax in fig.axes if ax.get_subplotspec() is not None]
    if width <= 0 or not placed:
        return                             # a divider's axes follow their parent
    home = [ax.get_position(original=True).frozen() for ax in placed]
    lo, hi = min(p.x0 for p in home), max(p.x1 for p in home)
    dl = dr = 0.0
    for _ in range(_PASSES):
        left, right = _tight_extents(fig, renderer)
        if left <= 0 and right <= 0:
            break
        dl += (left + _PAD * width) / width if left > 0 else 0.0
        dr += (right + _PAD * width) / width if right > 0 else 0.0
        if not _apply(placed, home, lo, hi, dl, dr):
            _apply(placed, home, lo, hi, 0.0, 0.0)
            break
        for ax, pos in zip(placed, home):
            setattr(ax, _HOME, pos)
