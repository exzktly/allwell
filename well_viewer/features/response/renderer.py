"""Drawing a response curve — pure matplotlib over what ``collect`` decided.

Reads a :class:`~well_viewer.features.response.collect.ResponseData` and paints
it. Every choice about *what* is in that data was made in ``collect``; the only
decisions here are about the axes.
"""

from __future__ import annotations

import logging

from well_viewer.features.response.collect import empty_reason, omission_caption
from well_viewer.features.response.response_compute import (
    curve_y, ec50_text, spans_enough_decades, zero_position,
)

_logger = logging.getLogger("well_viewer")

def draw_response(ctx, ax, data, *, log_x: bool | None = None,
                  title: str = "", ylabel: str = "",
                  variable_chosen: bool = True,
                  timepoints_chosen: bool = True,
                  fits: dict | None = None) -> None:
    """Paint *data* onto *ax*.

    *log_x* ``None`` asks the data — on once the positive values span two
    decades. Offering it from the data rather than defaulting to linear is the
    difference between a dose series that opens readable and one every user has
    to fix by hand before they can see anything.

    **The caption is drawn whenever there is one**, including on an otherwise
    perfect-looking plot. It is the only place a saved figure carries what it
    left out; the status line does not travel with the file.

    **A figure with nothing on it says why.** Bare axes are indistinguishable
    from a broken tab, and they are the same answer to six different mistakes;
    ``collect.empty_reason`` picks the sentence and this draws it in place of
    the axes rather than beside them.

    *fits* is ``{timepoint: FitResult}``. A fit that did not converge draws **no
    line at all** and puts ``EC50 — (no fit)`` in its legend entry: a dashed
    line through the points, or the optimiser's last iterate printed as a
    number, would both assert a shape the data did not support. Why each one
    was refused goes under the axes, where it travels with a saved figure.
    """
    ax.clear()
    reason = empty_reason(data, variable_chosen=variable_chosen,
                          timepoints_chosen=timepoints_chosen)
    if reason:
        _draw_empty_state(ax, reason, omission_caption(data))
        ax._aw_force_xlog = False
        from well_viewer.core.plot_style import apply_ax_style
        apply_ax_style(ax, title, "")
        return
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
        fit = (fits or {}).get(curve.timepoint)
        label = f"t={curve.timepoint:g}"
        if fit is not None:
            # The EC50 rides in the legend entry rather than in a box of its
            # own, so that two overlaid timepoints cannot get their numbers
            # swapped by a reader: the number sits against the swatch of the
            # curve it came from.
            label = f"{label} · {ec50_text(fit, data.unit)}"
        if on_axis:
            line = ax.errorbar([p.x for p in on_axis], [p.y for p in on_axis],
                               yerr=[p.err for p in on_axis],
                               marker="o", capsize=3, label=label)
            if fit is not None and fit.ok:
                _draw_fit_line(ax, fit, [p.x for p in on_axis], use_log,
                               _errorbar_colour(line))
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
                        off_scale_x if drew_off_scale else None,
                        _labels_the_user_typed(data))
    # ``_aw_force_xlog`` pins it, the way the scatter tab pins its own log/log:
    # ``apply_export_style_prefs`` re-reads the shared ``x_log`` on every
    # restyle and would otherwise reset an axis whose scale this tab chose from
    # the data, taking the off-scale column's ticks with it.
    ax._aw_force_xlog = bool(use_log and positives)

    unit = f" ({data.unit})" if data.unit else ""
    ax.set_xlabel(f"{data.variable_name}{unit}" if data.variable_name else "")
    # Also when there is only one curve, because a single curve's legend entry
    # is where its EC50 is written: suppressing it would drop the number the
    # fit exists to produce.
    if len([c for c in data.curves if c.points]) > 1 or fits:
        ax.legend(fontsize="small")

    caption = " · ".join(
        part for part in (omission_caption(data), _fit_caption(fits)) if part)
    if caption:
        # Under the axes rather than in the title: it is a note about the
        # figure's completeness, not what the figure is of, and a reader
        # scanning titles across a gallery should not have to parse it.
        ax.text(0.0, -0.18, caption, transform=ax.transAxes, fontsize="small",
                va="top", ha="left", wrap=True)

    from well_viewer.core.plot_style import apply_ax_style
    apply_ax_style(ax, title, ylabel)


def _errorbar_colour(container) -> str | None:
    """The colour matplotlib gave an ``errorbar``, so the fit can match it.

    Read back rather than chosen: taking the next colour from the cycle would
    paint the fit in the *following* curve's colour, which on a two-timepoint
    figure means each fitted line is drawn in the other timepoint's shade.
    """
    with_lines = getattr(container, "lines", None) or ()
    if with_lines and getattr(with_lines[0], "get_color", None):
        return with_lines[0].get_color()
    return None


def _draw_fit_line(ax, fit, xs, use_log: bool, colour) -> None:
    """A smooth fitted curve across the doses, and no further.

    Bounded by the doses tested rather than by the axes: a line running out to
    the edge of the view asserts a response at concentrations nobody gave, and
    the off-scale column at the left would have the curve passing through the
    axis break as though the vehicle were a dose.
    """
    import numpy as np

    lo, hi = min(xs), max(xs)
    if lo <= 0 or hi <= lo:
        return
    grid = (np.logspace(np.log10(lo), np.log10(hi), 200) if use_log
            else np.linspace(lo, hi, 200))
    ys = curve_y(fit, grid)
    if ys is None:
        return
    ax.plot(grid, ys, linestyle="-", linewidth=1.4, alpha=0.9, color=colour,
            zorder=1)


def _fit_caption(fits: dict | None) -> str:
    """Why each refused fit was refused, for under the axes.

    In the caption as well as the legend because the legend has room for
    ``(no fit)`` and not for the sentence, and "no fit" without a reason is the
    thing that sends a user to re-run a plate.
    """
    refused = [(t, f.reason) for t, f in sorted((fits or {}).items())
               if not f.ok and f.reason]
    if not refused:
        return ""
    if len(refused) == 1:
        return f"No fit at t={refused[0][0]:g}: {refused[0][1]}"
    return " ".join(f"No fit at t={t:g}: {reason}" for t, reason in refused)


def _draw_empty_state(ax, reason: str, caption: str) -> None:
    """The reason, centred, with the axes furniture taken away.

    The ticks and spines go because an empty pair of axes with a sentence
    floating in it reads as a plot that failed to draw; without them it reads
    as a message, which is what it is. The omission caption still goes
    underneath when there is one — it lists *which* groups were dropped, which
    the one-line reason above deliberately does not.
    """
    ax.text(0.5, 0.5, reason, transform=ax.transAxes, ha="center", va="center",
            fontsize="medium", wrap=True)
    if caption:
        ax.text(0.5, 0.30, caption, transform=ax.transAxes, ha="center",
                va="top", fontsize="small", wrap=True)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel("")


def _labels_the_user_typed(data) -> dict[float, str]:
    """``{x: the label as typed}``, for the doses that agree on one.

    ``ResponsePoint.label`` carries the value's label as the user wrote it —
    ``0.30``, ``1e-3``, ``10 µM`` — for exactly this: a tick that says what
    they typed rather than what the parse produced. Nothing read it, so the
    axis was relabelled from the parsed float and a series typed as a dilution
    came back reformatted.

    A dose is dropped from the map when two values parse to the same number and
    were typed differently (``0.3`` and ``0.30``): one tick cannot say both, so
    it says the number, which is at least true of both.
    """
    seen: dict[float, str] = {}
    dropped: set[float] = set()
    for curve in data.curves:
        for point in curve.points:
            text = (point.label or "").strip()
            if not text:
                continue
            x = float(point.x)
            if x in seen and seen[x] != text:
                dropped.add(x)
            seen.setdefault(x, text)
    return {x: text for x, text in seen.items() if x not in dropped}


def _tick_the_doses(ax, positives, off_scale_x: float | None,
                    typed: dict[float, str] | None = None) -> None:
    """One tick per dose, plus the off-scale column, and nothing else.

    matplotlib's log locator ticks the decades — 1, 10, 100 — which on a
    0.1/0.3/1/3/10 series labels two of the five points and invents three
    positions no well was given. A dose series is a short list of values the
    experimenter chose, so those are the ticks.

    Labelled with what the experimenter typed where that is unambiguous (see
    :func:`_labels_the_user_typed`), falling back to the parsed number.

    The off-scale column gets a dotted rule between it and the axis proper.
    That break is what makes the zero-dose point honest: without it the point
    sits where the axis says a real concentration is, one decade below the
    lowest dose, and a reader takes it for one.
    """
    import numpy as np

    typed = typed or {}
    doses = sorted(set(positives))
    ticks = list(doses)
    labels = [typed.get(d) or _tick_label(d) for d in doses]
    if off_scale_x is not None:
        ax.axvline(float(np.sqrt(off_scale_x * doses[0])),
                   linestyle=":", linewidth=0.8, alpha=0.6)
        ticks = [off_scale_x, *ticks]
        # The column stands for the zero-dose points, which sit at their own
        # x; the label they carry is the one the user typed for that value.
        labels = [typed.get(0.0) or "0", *labels]
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
