"""Value labels: the ``mean ± spread`` text drawn above a plotted point.

An opt-in overlay, off until the Properties sidebar's Overlays section turns it
on. Two pieces of state drive it, both on :class:`~well_viewer.core.plot_state.PlotState`
so every surface reads one answer: ``show_value_labels`` (the toggle) and
``value_label_format`` (a printf format for the numbers; empty picks one from
the magnitude, the same bargain the Heat Map's cell-value format strikes).

The module is shared rather than per-feature because the Bar, Line and
aggregate-Scatter renderers draw the same statistic and must print it the same
way: a label reading ``12.3 ± 1.4`` beside one reading ``12.34 ± 1.44`` for the
same well is the kind of divergence those surfaces exist to avoid. A scatter
point is the one that states *two* numbers — both of its axes carry a measured
statistic — which is what ``format_point_label`` and ``annotate_point`` are for;
everything about how each number is written is the same rules below. It holds no matplotlib import —
it only calls methods on an axes handed to it — and no Qt.

Two rules the formatting keeps, both of them about not stating more or less
than was measured:

* **The ± half appears only when a spread was computed and drawn.** With error
  bars switched off, or a spread that came back NaN (one replicate; FOV labels
  that do not identify the fields), the label is the mean alone. A ``± 0``
  printed over a bar whose spread was never computed says the replicates agreed
  — the exact confusion ``_mark_undefined_spread`` exists to prevent, restated
  in text.
* **The spread is formatted from its own magnitude, not the mean's.** Sharing
  one format is what a pair of numbers wants typographically and it is wrong
  arithmetically: the mean's format is chosen from the mean, and a whole-number
  mean takes ``%d``, which *truncates*. A replicate set of two wells counting 27
  and 33 events has a mean of exactly 30 and an SD of 4.24, and under a shared
  format the label read ``30 ± 4`` — understating the whisker drawn directly
  beneath it by 43%. A user-typed format still governs both halves, because
  there the shared look is the thing being asked for; even then a spread that
  format would round away keeps its own precision, since computed-as-zero and
  too-small-to-show are different facts.
"""

from __future__ import annotations

import math

#: Distance from the anchor point to the label, in typographic points. Matches
#: the offset ``_mark_undefined_spread`` uses for its ``n/a`` mark, so a panel
#: showing both keeps one baseline.
_LABEL_OFFSET_PT = 7

#: How much room the labels need, as multipliers a panel applies to its own
#: y-limits. Three numbers because a panel arrives at its top three ways, and
#: the label's own offset is in *ink* — seven typographic points above the
#: datum — which no data-space multiplier can express exactly. They live here,
#: with the thing that needs the room, so the Bar and Line panels cannot end up
#: reserving different amounts for the same text.
#:
#: ``LABEL_HEADROOM`` multiplies the tallest bar-plus-error (the bar panels'
#: own fit). ``LABEL_SPAN_HEADROOM`` multiplies a span matplotlib has already
#: autoscaled and given its 5% margin to (the line panels). ``LABEL_FRAC_CAP``
#: replaces the fraction panels' 1.05 ceiling, so a point at 1.0 is not
#: labelled over the panel title. Each is a widening only: a figure with labels
#: on must never crop data to make room for text.
#:
#: ``LABEL_FRAC_CAP`` is deliberately ``LABEL_HEADROOM`` applied to a full
#: fraction of 1.0, and not a smaller round number. A cap below that binds
#: before the headroom does, which made the Fraction panel the one panel
#: starved of the room every other panel was given — a bar at 1.0 got 16% of
#: its span where the Value panel got 22%, and its label was drawn over the
#: title.
LABEL_HEADROOM = 1.22
LABEL_SPAN_HEADROOM = 1.14
LABEL_FRAC_CAP = 1.22

#: Where a value label sits when the same bar also carries an ``n/a`` mark.
#: The mark is drawn at the same anchor, offset, alignment and point size, so
#: the two are pixel-identical at the default — this clears the mark's own line
#: (its offset plus its cap height) and stacks the value above it.
_LABEL_OFFSET_OVER_MARK_PT = 18

#: Point size for the label text. Deliberately smaller than the tick labels:
#: one label per bar or per marker is a lot of text on a busy panel, and the
#: Properties sidebar's font-size controls do not reach free text, so this is
#: the size it stays at.
_LABEL_FONT_PT = 7


def _auto_format(value: float) -> str:
    """A printf format for *value*, chosen from its magnitude.

    Three significant figures' worth of detail across the range the plots
    actually carry — raw intensities in the thousands, fractions between 0 and
    1, fold changes around 1, event counts anywhere — without printing a
    fifteen-digit float or an exponent for a number that reads fine as digits.

    A whole number keeps no decimals, which is what makes the events panel read
    as the count it is: ``30`` rather than ``30.00``. Zero is deliberately not
    treated as whole — it sits on the Fraction panel far more often than on a
    count one, and ``0`` printed beside ``0.420`` reads as a different kind of
    number rather than as the same measurement.
    """
    v = float(value)
    a = abs(v)
    if a == 0.0:
        return "%.3f"
    if a >= 1e5 or a < 1e-3:
        return "%.2e"
    if a >= 1 and v == int(v):
        return "%d"
    if a >= 1e4:
        return "%.0f"
    if a >= 100:
        return "%.1f"
    if a >= 1:
        return "%.2f"
    return "%.3f"


def _rounds_to_zero(text: str) -> bool:
    """True when *text* shows no significant digit — ``"0"``, ``"0.00"``, ``"0 nM"``.

    A digit scan rather than ``float(text)``: a user's format can carry units or
    a literal ``%``, and the float conversion raised on exactly those, so the
    guard below silently let ``± 0`` through for the formats most likely to
    produce it.
    """
    return not any(ch in "123456789" for ch in text)


def _apply_format(fmt: str, value: float) -> str:
    """*value* through the printf *fmt*, falling back to the automatic one.

    A user-typed format reaches here straight from a text field, so it can be
    anything — ``%q``, ``%``, a bare word. A bad format must not take the plot
    down with it, and it must not silently print nothing either: the number
    still appears, in the format the magnitude would have chosen.
    """
    try:
        return fmt % float(value)
    except (TypeError, ValueError):
        return _auto_format(value) % float(value)


def format_value_label(value, spread=None, *, fmt: str = "") -> str:
    """``"12.34 ± 1.20"`` for *value* ± *spread*, or ``""`` when unprintable.

    *spread* of ``None``, a non-finite spread, or one that is not positive
    yields the mean alone — see the module docstring on why a ``± 0`` is not an
    acceptable stand-in for a spread that was never computed.

    *fmt* is the user's printf format and governs both halves when set. Empty
    formats each half from **its own** magnitude, so the ± states the whisker
    that was drawn rather than the whisker rounded into the mean's precision.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v):
        return ""
    chosen = str(fmt or "") or _auto_format(v)
    head = _apply_format(chosen, v)
    if spread is None:
        return head
    try:
        s = float(spread)
    except (TypeError, ValueError):
        return head
    if not math.isfinite(s) or s <= 0:
        return head
    if fmt:
        # The user asked for this format; it governs both halves, so the pair
        # reads as one number the way they typed it.
        tail = _apply_format(chosen, s)
        # …except where that rounds the spread away. "1000 ± 0" claims a
        # measurement of zero for a spread that is merely small on this scale.
        if _rounds_to_zero(tail):
            tail = _apply_format(_auto_format(s), s)
    else:
        # No format asked for: the spread is its own number and is formatted
        # from its own magnitude. See the module docstring — inheriting a whole
        # mean's "%d" truncated 4.24 to 4.
        tail = _apply_format(_auto_format(s), s)
    return f"{head} ± {tail}"


def value_label_settings(ctx) -> tuple[bool, str]:
    """``(enabled, fmt)`` from *ctx*'s plot state.

    ``ctx=None`` — the batch-export bar path, and any unit test calling a
    renderer directly — reads as off, so a surface that has not been taught
    about the overlay cannot draw one by accident.
    """
    plot = getattr(getattr(ctx, "state", None), "plot", None)
    if plot is None:
        return (False, "")
    return (bool(getattr(plot, "show_value_labels", False)),
            str(getattr(plot, "value_label_format", "") or ""))


def annotate_value(ax, x: float, value, spread=None, *, fmt: str = "",
                   color=None, anchor=None, offset_pt: float | None = None,
                   inside: bool = False) -> None:
    """Draw the ``mean ± spread`` label for one point on *ax*.

    The label sits *outside* the datum: above it for a positive value, below
    for a negative one, so a bar that hangs below the axis (a subtractive fold
    change) is not labelled through its own body. *anchor* overrides the y the
    text is placed against — the bar renderers pass the top of the error cap so
    the text clears the whisker rather than landing on it.

    *offset_pt* overrides how far from the anchor the text sits. The bar
    renderer raises it for a bar that also carries an ``n/a`` mark: that mark is
    drawn at the same anchor with the same offset, alignment and point size, so
    at the default the two land on identical pixels and neither can be read —
    and a single well with no replicate set has an undefined spread on every
    bar, which is the commonest selection there is.

    *inside* flips the text to the other side of the anchor: used when the
    user has pinned this panel's upper y-limit and an outside label would be
    drawn over the panel title.

    ``annotation_clip=False`` keeps a label near the top of the axes visible
    rather than vanishing; the panels that draw these also widen their headroom
    (``LABEL_HEADROOM`` and its siblings above) so it rarely comes to that.
    """
    text = format_value_label(value, spread, fmt=fmt)
    if not text:
        return
    try:
        v = float(value)
        y = float(anchor) if anchor is not None else v
    except (TypeError, ValueError):
        return
    if not math.isfinite(y):
        y = v
    if not math.isfinite(y):
        return
    above = (v >= 0) if not inside else (v < 0)
    _place_label(ax, x, y, text, above=above, color=color, offset_pt=offset_pt)


def _place_label(ax, x: float, y: float, text: str, *, above: bool,
                 color=None, offset_pt: float | None = None) -> None:
    """Put *text* on *ax* at ``(x, y)``, offset *above* or below in ink.

    The one place a value label is actually drawn, shared by the bar / line
    labels and the scatter's per-point ones so a change to the type size, the
    offset or the theme tagging cannot reach one surface and miss another.
    """
    # A label with no colour of its own is chrome and takes the plot's ink; one
    # given a colour is wearing its trace's, which is data. Only the first is
    # tagged to follow a theme flip — repainting the second would substitute
    # the theme's ink for a value the plot is using to mean something. The
    # distinction matters because a capture flips the card to publication
    # *without* redrawing, so an untagged ink label reaches the file in the
    # screen theme's near-white.
    ink = color is None
    if ink:
        from well_viewer.core.plot_style import tokens_for
        color = tokens_for(ax)[1]
    off = _LABEL_OFFSET_PT if offset_pt is None else float(offset_pt)
    label = ax.annotate(
        text, (x, y), textcoords="offset points",
        xytext=(0, off if above else -off),
        ha="center", va="bottom" if above else "top",
        fontsize=_LABEL_FONT_PT, color=color, zorder=6,
        annotation_clip=False,
    )
    if ink:
        from well_viewer.core.plot_style import follows_theme
        follows_theme(label)


def format_point_label(x, y, x_spread=None, y_spread=None, *,
                       fmt: str = "") -> str:
    """``"(1200 ± 85, 0.420 ± 0.050)"`` for one scatter point, or ``""``.

    Both coordinates are stated because on a scatter both of them are the
    plotted statistic — unlike a bar or a line marker, where the x is a
    category or a time and only the y was measured. Labelling the y alone
    would drop half of what the point says.

    Each half goes through :func:`format_value_label`, so every rule that holds
    for a bar's label holds here too: a spread that was never computed prints
    no ±, and each number is formatted from its own magnitude unless the user
    typed a format. A point whose coordinate is unprintable yields no label at
    all rather than half of one, since a lone number beside a marker on two
    measured axes does not say which axis it belongs to.
    """
    xs = format_value_label(x, x_spread, fmt=fmt)
    ys = format_value_label(y, y_spread, fmt=fmt)
    if not xs or not ys:
        return ""
    return f"({xs}, {ys})"


def annotate_point(ax, x, y, *, x_spread=None, y_spread=None, fmt: str = "",
                   color=None) -> None:
    """Label one scatter point with both of its coordinates.

    The text sits above the point — clear of the y error cap when one was
    drawn, exactly as a bar's label clears its whisker — and below it for a
    point at a negative y, so a label is never drawn back through the data it
    describes.
    """
    text = format_point_label(x, y, x_spread, y_spread, fmt=fmt)
    if not text:
        return
    try:
        vx, vy = float(x), float(y)
    except (TypeError, ValueError):
        return
    if not (math.isfinite(vx) and math.isfinite(vy)):
        return
    anchor = vy
    try:
        s = float(y_spread) if y_spread is not None else float("nan")
        if math.isfinite(s) and s > 0:
            anchor = vy + s if vy >= 0 else vy - s
    except (TypeError, ValueError):
        anchor = vy
    _place_label(ax, vx, anchor, text, above=(vy >= 0), color=color)


def annotate_series(ax, xs, values, spreads=None, *, fmt: str = "",
                    color=None) -> None:
    """Label every point of one line-plot trace.

    *spreads* may be ``None`` (no band was drawn, so no ± is printed) or a
    sequence as long as *values*. The anchor is the top of the band, matching
    where the bar labels sit relative to their error caps.
    """
    for i, (x, v) in enumerate(zip(xs, values)):
        s = None
        if spreads is not None and i < len(spreads):
            s = spreads[i]
        anchor = None
        try:
            fv, fs = float(v), float(s) if s is not None else 0.0
            if math.isfinite(fv) and math.isfinite(fs) and fs > 0:
                anchor = fv + fs if fv >= 0 else fv - fs
        except (TypeError, ValueError):
            anchor = None
        annotate_value(ax, x, v, s, fmt=fmt, color=color, anchor=anchor)
