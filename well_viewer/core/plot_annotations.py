"""Value labels: the ``mean ± spread`` text drawn above a plotted point.

An opt-in overlay, off until the Properties sidebar's Overlays section turns it
on. Two pieces of state drive it, both on :class:`~well_viewer.core.plot_state.PlotState`
so every surface reads one answer: ``show_value_labels`` (the toggle) and
``value_label_format`` (a printf format for the numbers; empty picks one from
the magnitude, the same bargain the Heat Map's cell-value format strikes).

The module is shared rather than per-feature because the Bar and Line renderers
draw the same statistic and must print it the same way: a label reading
``12.3 ± 1.4`` beside one reading ``12.34 ± 1.44`` for the same well is the kind
of divergence the two surfaces exist to avoid. It holds no matplotlib import —
it only calls methods on an axes handed to it — and no Qt.

Two rules the formatting keeps, both of them about not stating more or less
than was measured:

* **The ± half appears only when a spread was computed and drawn.** With error
  bars switched off, or a spread that came back NaN (one replicate; FOV labels
  that do not identify the fields), the label is the mean alone. A ``± 0``
  printed over a bar whose spread was never computed says the replicates agreed
  — the exact confusion ``_mark_undefined_spread`` exists to prevent, restated
  in text.
* **A spread that would round to zero keeps its own precision.** The two halves
  otherwise share the format chosen for the mean, so they read as one number;
  but a mean of 1000 and a spread of 0.4 must not print ``1000 ± 0``, because
  computed-as-zero and too-small-to-show are different facts.
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
LABEL_HEADROOM = 1.22
LABEL_SPAN_HEADROOM = 1.14
LABEL_FRAC_CAP = 1.16

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
    acceptable stand-in for a spread that was never computed. *fmt* is the
    user's printf format; empty picks one from *value*'s magnitude and applies
    it to both halves so they read as one number.
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
    tail = _apply_format(chosen, s)
    # A spread the mean's format rounds away keeps its own: "1000 ± 0" claims a
    # measurement of zero for a spread that is merely small on this scale.
    try:
        if float(tail) == 0.0:
            tail = _apply_format(_auto_format(s), s)
    except ValueError:
        pass
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
                   color=None, anchor=None) -> None:
    """Draw the ``mean ± spread`` label for one point on *ax*.

    The label sits *outside* the datum: above it for a positive value, below
    for a negative one, so a bar that hangs below the axis (a subtractive fold
    change) is not labelled through its own body. *anchor* overrides the y the
    text is placed against — the bar renderers pass the top of the error cap so
    the text clears the whisker rather than landing on it.

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
    above = v >= 0
    if color is None:
        from well_viewer.core.plot_style import tokens_for
        color = tokens_for(ax)[1]
    ax.annotate(
        text, (x, y), textcoords="offset points",
        xytext=(0, _LABEL_OFFSET_PT if above else -_LABEL_OFFSET_PT),
        ha="center", va="bottom" if above else "top",
        fontsize=_LABEL_FONT_PT, color=color, zorder=6,
        annotation_clip=False,
    )


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
