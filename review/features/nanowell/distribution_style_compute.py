"""How the Distribution view draws each well's nanowells: as dots (a beeswarm), as a violin or as a histogram, how
smooth the violin is and how many bins the histogram has.

Qt-free. The page's **Show** control picks one of :data:`STYLES` and its **Smoothing** slider the violin's bandwidth
as a percentage of the automatic one (:data:`SMOOTH_MIN` to :data:`SMOOTH_MAX`, :data:`SMOOTH_DEFAULT` = the
automatic bandwidth itself). Both live on ``NanowellState`` (``dist_style``, ``violin_smooth``) and are read by the
renderer, by Batch Export's Nanowells mode, and by the Distribution window's "members not drawn" count, which must
agree with the renderer on which columns hold violins.

**Histogram** draws each well as a vertical histogram mirrored about its slot (``review.domain.column_histogram``):
every well's bars sum to 1, and every well in the figure shares one set of bins and one width scale, so widths
compare across wells and panels. Like a violin it draws no dots. **Bins** (:data:`BINS_MIN`–:data:`BINS_MAX`) is its
count; an integer metric bins on whole numbers.

**Auto** is what the view drew before it had the control: dots while a column holds :data:`DENSITY_ABOVE` wells or
fewer, violins past that (decision U2), since a slot shared by many wells is too narrow for a readable swarm.
**Beeswarm** and **Violin** draw one way whatever the count. A forced beeswarm in a crowded column draws what fits
and says how many dots it could not (the renderer's "+N"); a forced violin draws no dots, so single nanowells are
not clickable there and the footer says so.
"""

from __future__ import annotations

import math

from review.domain.column_histogram import BINS_DEFAULT, BINS_MAX, BINS_MIN, clamp_bins  # noqa: F401 — the page's

AUTO, BEESWARM, VIOLIN, HISTOGRAM = "auto", "beeswarm", "violin", "histogram"
#: The Show control's choices, in its order, with their labels.
STYLES = ((AUTO, "Auto"), (BEESWARM, "Beeswarm"), (VIOLIN, "Violin"), (HISTOGRAM, "Histogram"))
STYLE_KEYS = tuple(key for key, _label in STYLES)
#: Wells sharing a column beyond which Auto draws violins rather than dots (decision U2).
DENSITY_ABOVE = 4
#: The Smoothing slider's range and default, as a percentage of the automatic (Scott's rule) bandwidth. 100 draws
#: exactly the violin Auto drew before the control existed; below it follows the data more closely, above it
#: smooths more.
SMOOTH_MIN, SMOOTH_MAX, SMOOTH_DEFAULT = 25, 300, 100


def style_or_default(value) -> str:
    """*value* when it is one of :data:`STYLE_KEYS`, else Auto."""
    return value if value in STYLE_KEYS else AUTO


def clamp_smooth(value) -> int:
    """*value* as a whole percentage inside the slider's range; anything that is not a finite number is the
    default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return SMOOTH_DEFAULT
    return int(min(SMOOTH_MAX, max(SMOOTH_MIN, round(value))))


def density_above(style: str) -> float:
    """The number of wells per column beyond which *style* draws shapes rather than dots: :data:`DENSITY_ABOVE` for
    Auto (violins), none (every column) for Violin and Histogram, never for Beeswarm. The renderer draws a column as
    shapes when its well count is greater than this, and the window's count of members not drawn asks the same
    question."""
    style = style_or_default(style)
    if style in (VIOLIN, HISTOGRAM):
        return 0
    if style == BEESWARM:
        return math.inf
    return DENSITY_ABOVE


def smooths(style: str) -> bool:
    """Whether *style* can draw a violin, so Smoothing changes something: Auto and Violin."""
    return style_or_default(style) in (AUTO, VIOLIN)


def bandwidth(smooth) -> object:
    """The ``bw_method`` for ``Axes.violinplot`` at *smooth* percent: Scott's factor scaled by it, so 100 is
    matplotlib's own default and the scale is relative to each well's spread and count rather than an absolute
    width in the metric's units."""
    factor = clamp_smooth(smooth) / 100.0
    if factor == 1.0:
        return "scott"
    return lambda kde: kde.scotts_factor() * factor


def smooth_text(smooth) -> str:
    """The slider's readout and the figure's wording: ``"1.50×"``."""
    return f"{clamp_smooth(smooth) / 100.0:.2f}×"
