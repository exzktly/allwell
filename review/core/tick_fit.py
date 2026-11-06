"""Tick labels that do not run into each other, or off the figure.

A tick label is drawn at the size its renderer chose, and the room it has is
whatever the panel's width came to: the Heat Map's column numbers at 8 pt are
fine on 800 px and read "101112" on a phone, and the Multivariate correlation
matrix names its features in full along both axes, so two neighbours touch at
the desk as well. Neither renderer can know the width its figure will be given,
so the fit is made here, from the labels as drawn: if neighbouring labels
overlap, or one is cut off by the figure's edge, shrink them a step at a time,
then break a name at its spaces, and when that still does not fit, stand an
x-axis's labels on end. Nothing is dropped and nothing is
elided: a label that vanishes states a different plot, and a tick that reads
"GFP Mean Inten…" names two features at once.

Pure matplotlib, no Qt, no app state, and no knowledge of which layout the
figure is in: the decision is a function of the figure's own measured text.
``plot_style`` calls :func:`fit_tick_labels` once the layout has settled.
"""

from __future__ import annotations

import textwrap

from matplotlib.ticker import FixedLocator

#: Sizes tried, as a share of the renderer's, before the labels are turned.
#: Dimensionless, so a phone's larger DPI and a desk's smaller one are the same
#: question; below the last, a tick label stops being comfortable to read.
SHRINK_STEPS = (0.9, 0.8, 0.7)

#: How many lines a label with spaces in it may be broken onto, tried in order,
#: before the labels are turned.
WRAP_LINES = (2, 3)

#: Clearance, in pixels, below which two labels count as touching: the same
#: tolerance the page-render harness uses, so what it calls clear this calls
#: clear.
_TOUCH_PX = 1.5

#: On an axis: ``(size, rotation, ha, text)`` the renderer gave its labels, and
#: the values the last fit wrote. A label still carrying the second was not
#: touched since, so the next fit starts again from the first. *text* is the
#: label strings when the axis holds fixed ones (``set_xticklabels``) and
#: ``None`` when a formatter writes them, which is never rewritten.
_SOURCE = "_aw_tick_src"
_APPLIED = "_aw_tick_applied"


def _drawn(axis, renderer, view) -> list:
    """``(location, label)`` of the labels an axis actually draws, in order along it."""
    lo, hi = sorted(view)
    out = []
    for tick, label in zip(axis.get_major_ticks(), axis.get_majorticklabels()):
        if label.get_visible() and label.get_text().strip() and lo <= tick.get_loc() <= hi:
            out.append((tick.get_loc(), label))
    out.sort(key=lambda item: item[0])
    return out


def _collide(drawn, renderer, fig) -> bool:
    """Whether the labels do not fit: two neighbours' boxes overlap, or one runs
    past the figure's left or right edge (cut off, which constrained layout
    leaves when the labels are wider than the room it has to give).

    Only the sideways edges: a label that grows downward is room the layout
    can make, a label that is wider than the figure is not."""
    boxes = [label.get_window_extent(renderer) for _loc, label in drawn]
    if any(b.x0 < -_TOUCH_PX or b.x1 > fig.bbox.width + _TOUCH_PX for b in boxes):
        return True
    for a, b in zip(boxes, boxes[1:]):
        if (min(a.x1, b.x1) - max(a.x0, b.x0) > _TOUCH_PX
                and min(a.y1, b.y1) - max(a.y0, b.y0) > _TOUCH_PX):
            return True
    return False


def _texts(axis) -> tuple[str, ...] | None:
    """The label strings of an axis whose ticks are fixed, else ``None``.

    Only an axis with a fixed locator has labels a caller chose; anywhere else
    a formatter writes them afresh on every draw, so a rewritten string would
    last until the next one.
    """
    if isinstance(axis.get_major_locator(), FixedLocator):
        return tuple(label.get_text() for label in axis.get_majorticklabels())
    return None


def _look(axis, label) -> tuple:
    return (float(label.get_fontsize()), float(label.get_rotation()), label.get_ha(), _texts(axis))


def _set_look(axis, look) -> None:
    size, rotation, ha, texts = look
    axis.set_tick_params(which="major", labelsize=size, labelrotation=rotation)
    if texts is not None and _texts(axis) != texts:
        axis.set_ticklabels(list(texts))
    for label in axis.get_majorticklabels():
        label.set_ha(ha)


def _wrapped(texts, lines: int) -> tuple[str, ...]:
    """*texts* with each one broken at its spaces into at most *lines* lines."""
    def one(text: str) -> str:
        if " " not in text.strip():
            return text
        return textwrap.fill(text, width=max(1, -(-len(text) // lines)), break_long_words=False)
    return tuple(one(t) for t in texts)


def _fit_axis(ax, axis, view, renderer, *, can_turn: bool) -> None:
    # As ``Axes.draw`` places it: an axes a divider positions (the Heat Map's
    # grid and its colour bar) is where its locator puts it, not where its own
    # position says.
    locator = ax.get_axes_locator()
    ax.apply_aspect(locator(ax, renderer) if locator is not None else None)
    drawn = _drawn(axis, renderer, view)
    if len(drawn) < 2:
        return
    current = _look(axis, drawn[0][1])
    kept = getattr(axis, _SOURCE, None)
    if kept is not None and getattr(axis, _APPLIED, None) == current:
        source = kept
        if current != source:
            _set_look(axis, source)
    else:
        source = current
    setattr(axis, _SOURCE, source)
    setattr(axis, _APPLIED, source)
    if not _collide(_drawn(axis, renderer, view), renderer, ax.figure):
        return
    size, rotation, ha, texts = source

    def attempt(look) -> bool:
        _set_look(axis, look)
        setattr(axis, _APPLIED, _look(axis, axis.get_majorticklabels()[0]))
        return not _collide(_drawn(axis, renderer, view), renderer, ax.figure)

    for step in SHRINK_STEPS:
        if attempt((size * step, rotation, ha, texts)):
            return
    if texts is not None and rotation == 0.0:
        for lines in WRAP_LINES:
            wrapped = _wrapped(texts, lines)
            if wrapped != texts and attempt((size * SHRINK_STEPS[1], rotation, ha, wrapped)):
                return
    if can_turn:
        # Stood on end, centred on the tick rather than hanging off its side.
        attempt((size * SHRINK_STEPS[-1], 90.0, "center", texts))


def fit_tick_labels(fig) -> None:
    """Make neighbouring tick labels on every axis of *fig* clear of each other.

    Measured as drawn, so it belongs after the figure's layout has settled. A
    figure whose labels already clear is left exactly as it was, and one fitted
    earlier whose labels now clear (the figure grew) gets its own size back.
    """
    renderer = fig.canvas.get_renderer()
    for ax in fig.axes:
        _fit_axis(ax, ax.xaxis, ax.get_xlim(), renderer, can_turn=True)
        _fit_axis(ax, ax.yaxis, ax.get_ylim(), renderer, can_turn=False)
