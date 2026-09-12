"""Put every figure's plotting area in the same place on every slide.

A deck built one slide at a time scales each figure to fill the space it is
given, which is the obvious rule and the wrong one. A saved capture is cropped
to its own ink, so the page a bar chart with a five-character y axis produces is
a different shape from the page the same chart produces after the numbers grow
to seven digits — and scaling each of those to fill one box puts their *axes* at
different sizes, in different places. Two plots authored at exactly the same
figure size come out of the export as figures that cannot be laid beside each
other, which is what makes assembling slides into a larger composite figure
impossible.

So the deck anchors on the plotting area rather than on the page. Each capture
records where its axes sit on its page (``fig_clipboard.AXES_RECT_MARKER``), and
this module turns those records into one **anchor box** — a rectangle in slide
coordinates that every figure's axes is scaled and centred into. Everything
outside the axes (tick labels, titles, legends) hangs off the anchor box into
the space around it.

The anchor is sized from the whole deck rather than fixed, because the space the
decorations need is not knowable in advance: one figure with a legend outside the
axes needs a third of its width for it, and a rule generous enough for that would
waste the same third on every plot that has no legend. Taking the largest margin
any plot in the deck asks for — as a proportion of that plot's own axes — gives
the biggest anchor box that still leaves every figure's decorations inside the
body of its slide.

No figure is therefore drawn any smaller than filling the slide would have drawn
it *on its own*: a deck of one plot has its own margins as the maxima, and the
anchor box works out to the same scale the letterbox chose. What changes for that
single plot is only what gets centred in whatever space is left over — the
plotting area rather than the page — which is the same rule that lines two plots
up when there are two.

Qt-free and app-free, like the rest of the exporter's back half.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnchorBox:
    """Where a figure's plotting area goes, in EMU on the slide."""

    x: int
    y: int
    cx: int
    cy: int


def _margins(frac: tuple[float, float, float, float]) -> tuple[float, ...]:
    """How much room a figure's decorations need, per unit of its axes.

    Expressed against the axes rather than against the page because the axes is
    what gets scaled to a known size: a figure whose y-axis labels are a fifth as
    wide as its plotting area needs a fifth of the anchor box's width to the left
    of it, whatever the page around it measured.
    """
    x0, y0, x1, y1 = frac
    width = x1 - x0
    height = y1 - y0
    return (x0 / width, (1.0 - x1) / width,
            y0 / height, (1.0 - y1) / height)


def plan_anchor(fractions, *, x: int, y: int, cx: int, cy: int) -> AnchorBox | None:
    """The one anchor box a deck of these figures shares.

    *fractions* are the recorded axes rectangles of every plot that has one —
    ``(x0, y0, x1, y1)`` as fractions of that plot's page. ``None`` when the deck
    holds no such plot, which is the caller's signal to fall back to letterboxing
    each page on its own.
    """
    frames = [f for f in fractions if f is not None]
    if not frames or cx <= 0 or cy <= 0:
        return None
    margins = [_margins(f) for f in frames]
    left = max(m[0] for m in margins)
    right = max(m[1] for m in margins)
    top = max(m[2] for m in margins)
    bottom = max(m[3] for m in margins)
    width = cx / (1.0 + left + right)
    height = cy / (1.0 + top + bottom)
    return AnchorBox(x=int(round(x + left * width)),
                     y=int(round(y + top * height)),
                     cx=int(round(width)), cy=int(round(height)))


def axes_rect_points(frac: tuple[float, float, float, float],
                     width_pt: float, height_pt: float) -> tuple[float, ...]:
    """A recorded axes fraction as points on a page of *width_pt* × *height_pt*."""
    x0, y0, x1, y1 = frac
    return (x0 * width_pt, y0 * height_pt, x1 * width_pt, y1 * height_pt)
