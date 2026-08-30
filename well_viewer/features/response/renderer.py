"""Drawing a response curve — pure matplotlib over what ``collect`` decided.

Reads a :class:`~well_viewer.features.response.collect.ResponseData` and paints
it. Every choice about *what* is in that data was made in ``collect``; the only
decisions here are about the axes.
"""

from __future__ import annotations

import logging

from well_viewer.features.response.collect import omission_caption
from well_viewer.features.response.response_compute import (
    spans_enough_decades, zero_position,
)

_logger = logging.getLogger("well_viewer")

#: Where the off-scale column of non-numeric members is drawn on a **linear**
#: axis, as a fraction of the data's span to the left of the lowest point.
_LINEAR_OFFSET_FRACTION = 0.12


def draw_response(ctx, ax, data, *, log_x: bool | None = None,
                  title: str = "", ylabel: str = "") -> None:
    """Paint *data* onto *ax*.

    *log_x* ``None`` asks the data — on once the positive values span two
    decades. Offering it from the data rather than defaulting to linear is the
    difference between a dose series that opens readable and one every user has
    to fix by hand before they can see anything.

    **The caption is drawn whenever there is one**, including on an otherwise
    perfect-looking plot. It is the only place a saved figure carries what it
    left out; the status line does not travel with the file.
    """
    ax.clear()
    positives = [p.x for c in data.curves for p in c.points if p.x > 0]
    use_log = spans_enough_decades(positives) if log_x is None else bool(log_x)
    # Where a zero (or negative) dose is drawn on a log axis. One decade below
    # the lowest real dose, so the gap reads as "off the scale" rather than as a
    # measured point that happens to be small.
    off_scale_x = zero_position(positives) if use_log else None

    def _x(value: float) -> float:
        return off_scale_x if (use_log and value <= 0) else value

    drew_off_scale = False
    for curve in data.curves:
        if not curve.points:
            continue
        on_axis = [p for p in curve.points if not (use_log and p.x <= 0)]
        off = [p for p in curve.points if use_log and p.x <= 0]
        label = f"t={curve.timepoint:g}"
        if on_axis:
            ax.errorbar([p.x for p in on_axis], [p.y for p in on_axis],
                        yerr=[p.err for p in on_axis],
                        marker="o", capsize=3, label=label)
        if off and off_scale_x is not None:
            # Same colour as its own curve would be too clever: this point is
            # deliberately not on the curve, and joining it with a line would
            # assert a slope across a break in the axis.
            ax.errorbar([_x(p.x) for p in off], [p.y for p in off],
                        yerr=[p.err for p in off], fmt="o", capsize=3,
                        markerfacecolor="none")
            drew_off_scale = True

    if use_log and positives:
        ax.set_xscale("log")
        _tick_the_doses(ax, positives,
                        off_scale_x if drew_off_scale else None)

    unit = f" ({data.unit})" if data.unit else ""
    ax.set_xlabel(f"{data.variable_name}{unit}" if data.variable_name else "")
    if len([c for c in data.curves if c.points]) > 1:
        ax.legend(fontsize="small")

    caption = omission_caption(data)
    if caption:
        # Under the axes rather than in the title: it is a note about the
        # figure's completeness, not what the figure is of, and a reader
        # scanning titles across a gallery should not have to parse it.
        ax.text(0.0, -0.18, caption, transform=ax.transAxes, fontsize="small",
                va="top", ha="left", wrap=True)

    from well_viewer.core.plot_style import apply_ax_style
    apply_ax_style(ax, title, ylabel)


def _tick_the_doses(ax, positives, off_scale_x: float | None) -> None:
    """One tick per dose, plus the off-scale column, and nothing else.

    matplotlib's log locator ticks the decades — 1, 10, 100 — which on a
    0.1/0.3/1/3/10 series labels two of the five points and invents three
    positions no well was given. A dose series is a short list of values the
    experimenter chose, so those are the ticks.

    The off-scale column gets a dotted rule between it and the axis proper.
    That break is what makes the zero-dose point honest: without it the point
    sits where the axis says a real concentration is, one decade below the
    lowest dose, and a reader takes it for one.
    """
    import numpy as np

    doses = sorted(set(positives))
    ticks = list(doses)
    labels = [_tick_label(d) for d in doses]
    if off_scale_x is not None:
        ax.axvline(float(np.sqrt(off_scale_x * doses[0])),
                   linestyle=":", linewidth=0.8, alpha=0.6)
        ticks = [off_scale_x, *ticks]
        labels = ["0", *labels]
        ax.set_xlim(left=off_scale_x / 2.0)
    else:
        ax.set_xlim(left=doses[0] / 2.0)
    ax.set_xlim(right=doses[-1] * 2.0)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    # The minor locator keeps drawing unlabelled decade subdivisions between
    # the doses, which on a break-and-column axis reads as data.
    ax.set_xticks([], minor=True)


def _tick_label(value: float) -> str:
    """A dose tick without the exponent noise ``1e-01`` for 0.1."""
    if value == 0:
        return "0"
    return f"{value:g}"
