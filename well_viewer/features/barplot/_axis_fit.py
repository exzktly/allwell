"""Y-limit fitting and tick selection for the bar plot's three panels.

Pure axis arithmetic over a list of ``BarItem`` records: no Qt, no app state,
one matplotlib call each. Extracted from ``controller.render_bar_items``, which
is under a line budget, and because the two decisions here are both ones the
codebase has got wrong by copying a sibling's rule without checking it applies:

* the y-limit must scan the same items the panel *draws*, so a panel whose
  bar is drawn from ``n_above`` cannot scan on ``has_mean``;
* whether the events axis takes integer ticks depends on **two** facts, and the
  other function that draws that panel only has to ask one of them.

Both are argued at their call sites below.
"""

from __future__ import annotations

import math

from well_viewer.core.plot_annotations import LABEL_FRAC_CAP, LABEL_HEADROOM
from well_viewer.domain.numeric import is_number

#: Headroom above the tallest bar-plus-error, so the error caps are not clipped.
HEADROOM = 1.08

#: The fraction panel's ceiling. A raw fraction cannot exceed 1, so the axis
#: stops just above it. Under fold change it does not apply at all — the field
#: is then a ratio or a difference.
FRAC_CAP = 1.05


def tops_lows(items, get_val, get_spread) -> tuple[list[float], list[float]]:
    """``(tops, lows)`` — each drawable item's value ± its spread.

    Items whose value is absent or non-finite are skipped, and a non-finite or
    negative spread counts as zero: an undefined spread must not widen the axis
    as though it had been measured.
    """
    tops: list[float] = []
    lows: list[float] = []
    for it in items:
        v = get_val(it)
        if v is None or not math.isfinite(v):
            continue
        s = get_spread(it) or 0.0
        if not math.isfinite(s) or s < 0:
            s = 0.0
        tops.append(v + s)
        lows.append(v - s)
    return tops, lows


def autofit(ax, tops, lows, *, cap=None, floor_hi=None,
            headroom: float = HEADROOM, pad_below: bool = False) -> None:
    """Set *ax*'s y-limits from *tops* / *lows*, on an honest zero baseline.

    The axis dips below zero only for genuine negatives, so a bar of zero
    height sits on the axis rather than floating above it. *cap* bounds the top
    (the fraction panel, which cannot exceed 1 unless fold change makes it a
    ratio); *floor_hi* raises it (the events panel, so a plate of small counts
    does not get a one-unit-tall axis).

    The headroom is added to the **span**, not multiplied into the top. On the
    ordinary all-positive panel the baseline is zero and the two are the same
    arithmetic, but for a subtractive fold change every top is negative and
    multiplying moved the ceiling *down* — a crop, growing worse the more
    headroom was asked for, which is the opposite of what the argument means.

    *pad_below* extends the floor by the same amount. Only the value-label
    overlay asks for it: a label on a negative bar is drawn *below* the bar, so
    the room it needs is at the bottom of the panel, and the top padding that
    serves every positive bar does nothing for it.
    """
    if not tops:
        return
    raw_hi = max(tops)
    lo = min([0.0] + lows)
    # *floor_hi* is applied **before** the padding is measured, not after. It
    # can multiply the span several times over on a panel of small or negative
    # counts, and a pad taken from the pre-floor span is then a fraction of a
    # fraction — on a negative events panel it came to 4% of what was drawn,
    # and the labels fell out of the bottom.
    if floor_hi is not None:
        raw_hi = max(raw_hi, floor_hi)
    pad = abs(raw_hi - lo) * (headroom - 1.0)
    hi = raw_hi + pad
    if cap is not None:
        hi = min(hi, cap)
    if pad_below and lo < 0:
        lo -= pad
    if hi > lo:
        ax.set_ylim(lo, hi)


def events_axis_is_integer(*, requested: bool | None, per_fov: bool,
                           fc_active: bool) -> bool:
    """Whether the events-above-threshold axis should take integer ticks.

    N is a count of events, so integer ticks are the default. Two things make
    the drawn value something other than a count, and **both** have to be
    asked, because either alone gives the wrong answer half the time:

    * *per_fov* — Aggregate-FOVs mode, where the bar is a per-FOV *mean* of
      counts;
    * *fc_active* — fold change, where the bar is a ratio or a difference of
      them.

    ``renderer.draw_per_well_n_bars`` asks only the first, and is right to: its
    own docstring records that nothing it draws is fold-changed, and it is never
    handed a fold-change state. Copying that rule wholesale into the path that
    *is* fold-changed rounded a measured fold change of 1.35 to ticks of 0, 1,
    2.

    *requested* is a caller's explicit choice (the batch-export panel passes one
    derived from the FOV toggle alone). Fold change is applied **after** it,
    never before: a fold-changed count is a ratio whoever asked for the panel.
    """
    integer = (not per_fov) if requested is None else bool(requested)
    return integer and not fc_active


def fit_bar_panels(ax_mean, ax_frac, ax_n, items, *, fc_active: bool,
                   n_axis_integer: bool | None, per_fov: bool,
                   value_labels: bool = False) -> None:
    """Fit all three bar panels and set the events axis' tick locator.

    *value_labels* says the ``mean ± spread`` overlay is on, which is a fact
    about the axis and not only about the text: the label is drawn above the
    error cap, so a panel fitted to the cap alone puts it over the title.
    """
    _hr = LABEL_HEADROOM if value_labels else HEADROOM
    autofit(ax_mean, *tops_lows(
        items,
        lambda it: it.mean if it.has_mean else float("nan"),
        lambda it: it.spread), headroom=_hr, pad_below=value_labels)
    # The cap applies only when FC is off — under FC the frac may be a ratio > 1.
    _cap = LABEL_FRAC_CAP if value_labels else FRAC_CAP
    autofit(ax_frac, *tops_lows(
        items,
        lambda it: it.frac if it.has_frac else float("nan"),
        lambda it: it.frac_spread), cap=None if fc_active else _cap,
        headroom=_hr, pad_below=value_labels)
    if ax_n is None:
        return
    # Scanned on ``n_above`` itself, not on ``has_mean``. The mean is NaN
    # exactly when nothing passed the threshold, so scanning on it left every
    # measured-zero bar out of the y-limit — the same coupling the bar's own
    # draw gate had.
    autofit(ax_n, *tops_lows(
        items,
        lambda it: float(it.n_above) if is_number(it.n_above) else float("nan"),
        lambda it: it.n_above_spread), floor_hi=1.0, headroom=_hr,
        pad_below=value_labels)
    try:
        from matplotlib.ticker import MaxNLocator
    except ImportError:
        return
    ax_n.yaxis.set_major_locator(MaxNLocator(integer=events_axis_is_integer(
        requested=n_axis_integer, per_fov=per_fov, fc_active=fc_active)))
