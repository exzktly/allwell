"""The arithmetic of zooming and panning a figure on the Nanowells page: new axis limits from old ones.

Qt-free and canvas-free, so every number a wheel notch or a drag produces is tested without an event loop. The
Array view zooms its *region* by :func:`held_factor` and ``full_montage_compute.zoom_region``; every Identity
sub-view zooms its axes' limits by :func:`wheel_limits` and pans them by :func:`pan_limits`, and the axes drawn as
one view of the same place (*Side by side*'s two montages, Diagnostics' two crops) are moved together
(:func:`linked`).

Limits are ``(xlim, ylim)`` pairs as matplotlib gives them: ``ylim`` is ``(bottom, top)``, so on an image drawn with
y down it runs from the larger value to the smaller. Nothing here assumes either order: every step is linear in each
end, so it holds for both.
"""

from __future__ import annotations

from review.features.nanowell.full_montage_compute import zoom_region

#: One wheel notch zooms by this factor (under 1 zooms in); the Array view's *−* and *+* buttons step by two notches.
WHEEL_STEP = 0.8
BUTTON_STEP = WHEEL_STEP ** 2
#: How far a view zooms, against its full view: out to a quarter of it, in to 500 times it — past either the view is
#: a speck or one pixel of the image is wider than the screen.
ZOOM_OUT_LIMIT, ZOOM_IN_LIMIT = 0.25, 500.0


def held_factor(now: float | None, factor: float) -> float:
    """*factor* (under 1 zooms in) shortened so the zoom after it stays within :data:`ZOOM_OUT_LIMIT` and
    :data:`ZOOM_IN_LIMIT` of the full view, where *now* is the zoom before it (1 is the full view, 2 shows half the
    width; ``None``: not known, so not held). ``1.0`` when no step is allowed: a view already at or past a limit is
    not stepped further past it, and never the opposite way to the one asked for."""
    if now is None:
        return factor
    if (factor > 1 and now <= ZOOM_OUT_LIMIT) or (factor < 1 and now >= ZOOM_IN_LIMIT):
        return 1.0
    target = min(max(now / factor, ZOOM_OUT_LIMIT), ZOOM_IN_LIMIT)
    return now / target


def zoom_against(xlim, home_xlim) -> float | None:
    """How far *xlim* is zoomed against *home_xlim* (the full view): 1 is the full view, 2 shows half its width;
    ``None`` when either has no width."""
    width, home = float(xlim[1]) - float(xlim[0]), float(home_xlim[1]) - float(home_xlim[0])
    return home / width if width and home else None


def zoom_limits(xlim, ylim, at, factor: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """*xlim*, *ylim* scaled by *factor* (under 1 zooms in) about the data point *at*, which keeps its place on
    screen: the distance from *at* to each end is multiplied by *factor*."""
    left, bottom, right, top = zoom_region((xlim[0], ylim[0], xlim[1], ylim[1]), factor, at)
    return (float(left), float(right)), (float(bottom), float(top))


def wheel_limits(xlim, ylim, at, steps: float, home_xlim=None) -> tuple[tuple[float, float], tuple[float, float]]:
    """The limits after *steps* wheel notches (positive zooms in, as matplotlib's ``event.step`` is positive for the
    wheel turned away) about the data point *at*: a factor of ``WHEEL_STEP ** steps``, held within the zoom limits
    against *home_xlim* when it is given (:func:`held_factor`). Unchanged when no step is allowed."""
    factor = WHEEL_STEP ** float(steps)
    if home_xlim is not None:
        factor = held_factor(zoom_against(xlim, home_xlim), factor)
    if factor == 1.0:
        return (float(xlim[0]), float(xlim[1])), (float(ylim[0]), float(ylim[1]))
    return zoom_limits(xlim, ylim, at, factor)


def pan_limits(xlim, ylim, dx: float, dy: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """The limits after the pointer was dragged by (*dx*, *dy*) data units: the picture follows the pointer, so the
    limits move the other way, by (−*dx*, −*dy*)."""
    return ((float(xlim[0]) - dx, float(xlim[1]) - dx), (float(ylim[0]) - dy, float(ylim[1]) - dy))


def linked(links, name: str) -> tuple[str, ...]:
    """The axes that move with *name*: the group of *links* (groups of axes names) holding it, or *name* alone."""
    for group in links or ():
        if name in group:
            return tuple(group)
    return (name,)
