"""A limit the user typed survives everything else the style pass does.

Axis limits are applied early in ``apply_export_style_prefs``, because the
steps after them — the log scale, the tick locators, the legend, the layout
engine — all want a settled range to lay themselves out against. That ordering
is right and it is also a hazard: matplotlib re-autoscales on a good many of
those, and when it does, the failure is silent by construction. The number the
user typed is still in the Properties field and still in the store; the plot is
simply drawn somewhere else. It reads as the setting not working, which is why
a user hits it as "my limits keep coming back and I have to retype the same
values", and why nothing in the app said otherwise.

So the limits are re-asserted at the end of the pass, and a mismatch is
**logged** rather than quietly corrected: §7's rule that degraded output must
not be indistinguishable from good output applies to the app's own diagnosis
too. Something autoscaling over a typed limit is a defect with a cause, and a
silent re-application would hide it from the next reader exactly as effectively
as it is hidden now.
"""

from __future__ import annotations

import logging

import pytest

from review.core.plot_style import (
    DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs,
)


def _fig_with_limits(**over):
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1.0, 10.0, 100.0], [10.0, 10.0, 10.0])
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs.update(over)
    return fig, ax, prefs


def test_a_typed_limit_is_applied():
    """The baseline, so a pass below cannot be a pass over nothing."""
    fig, ax, prefs = _fig_with_limits(y_lim_min="1", y_lim_max="1000")
    apply_export_style_prefs(fig, prefs)
    assert ax.get_ylim() == pytest.approx((1.0, 1000.0))


def test_the_log_step_does_not_lose_them():
    """A typed range survives the scale change, which is where it was suspected.

    Measured rather than assumed, and it is a **pass either way**: removing the
    hold leaves this green, so the real ``set_yscale`` between the limits and
    the end of the pass does not autoscale over them. Kept as the record of
    that — the ordering is the obvious suspect for a report of lost limits and
    this is what rules it out — rather than as a guard, which is what the
    staged test below is.
    """
    fig, ax, prefs = _fig_with_limits(y_lim_min="1", y_lim_max="1000",
                                      y_log=True)
    apply_export_style_prefs(fig, prefs)
    assert ax.get_yscale() == "log"
    assert ax.get_ylim() == pytest.approx((1.0, 1000.0))


def test_a_lost_limit_is_reported_rather_than_silently_fixed(monkeypatch, caplog):
    """The re-assertion must not become a way of not noticing.

    A limit that had to be put back names the axis and both numbers in the log,
    so "my limits keep coming back wrong" has something to read. Staged by
    autoscaling the axis from inside the pass, after the limits are applied.
    """
    from review.core import plot_style as ps

    fig, ax, prefs = _fig_with_limits(y_lim_min="1", y_lim_max="1000")

    real = ps.apply_log_minor_ticks

    def _autoscaling(axes, which, base):
        axes.autoscale(enable=True, axis="y")
        axes.set_ylim(auto=True)
        axes.autoscale_view()
        return real(axes, which, base)

    monkeypatch.setattr(ps, "apply_log_minor_ticks", _autoscaling)
    prefs["y_log"] = True
    with caplog.at_level(logging.WARNING, logger="review"):
        apply_export_style_prefs(fig, prefs)

    assert ax.get_ylim() == pytest.approx((1.0, 1000.0)), (
        "the typed limit was not restored")
    said = [r.getMessage() for r in caplog.records if "limits" in r.getMessage()]
    assert said, "the limits were silently put back with nothing logged"
    assert "y limits" in said[0], said[0]


def test_an_untyped_axis_is_left_alone(caplog):
    """No limit typed, nothing to hold, and nothing to say about it — otherwise
    every figure in the app would log on every redraw."""
    fig, ax, prefs = _fig_with_limits()
    with caplog.at_level(logging.WARNING, logger="review"):
        apply_export_style_prefs(fig, prefs)
    assert not [r for r in caplog.records if "limits" in r.getMessage()]


def test_one_typed_bound_holds_and_the_other_stays_free():
    """A user who pins only the top keeps an autoscaled floor — the hold must
    not turn a half-specified range into a fully pinned one."""
    fig, ax, prefs = _fig_with_limits(y_lim_max="1000")
    apply_export_style_prefs(fig, prefs)
    assert ax.get_ylim()[1] == pytest.approx(1000.0)
    assert ax.get_ylim()[0] < 1000.0


# ── a bound a log axis cannot take ──────────────────────────────────────────
#
# The report this file was opened for, once it had a reproduction: **Y limits
# 0 to 1 with the y axis logarithmic**, which is the obvious range to type on a
# *fraction* axis and the one number a log axis cannot show. It drew every trace
# in the top thousandth of the figure, and re-typing the same two numbers fixed
# it — which is what made it look like the control not working rather than like
# the value being impossible.
#
# The asymmetry is matplotlib's: a non-positive bound is refused **only when the
# axis is already logarithmic**. On every redraw ``ax.clear()`` leaves the axis
# linear, so applying the limits before the scale meant the 0 was accepted in
# silence and survived into an axis where it means -inf.

def _fraction_figure(y_log: bool, lo: str, hi: str):
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ax.plot([1.0, 10.0, 100.0], [0.2, 0.6, 0.9])   # a fraction, 0..1
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs.update(x_log=True, y_log=y_log, y_lim_min=lo, y_lim_max=hi)
    apply_export_style_prefs(fig, prefs)
    fig.canvas.draw()
    return fig, ax


def _heights(ax, xs, ys):
    """Where the points sit inside the axes: 0 is the floor, 1 the ceiling.

    The assertion is on this rather than on the limits, because "crushed to the
    top" is what the user saw and the limits alone do not say it: ``(0.0, 1.0)``
    on a log axis looks like a perfectly ordinary range right up until it is
    drawn.
    """
    inv = ax.transAxes.inverted()
    return [float(inv.transform(ax.transData.transform((x, y)))[1])
            for x, y in zip(xs, ys)]


def test_zero_to_one_on_a_log_axis_does_not_crush_the_traces():
    _fig, ax = _fraction_figure(y_log=True, lo="0", hi="1")
    heights = _heights(ax, (1.0, 10.0, 100.0), (0.2, 0.6, 0.9))
    assert max(heights) - min(heights) > 0.5, (
        f"the traces are flattened into a band {max(heights) - min(heights):.4f} "
        f"of the figure tall, at {heights}")
    assert min(heights) < 0.5, f"nothing is drawn in the lower half: {heights}"


def test_the_impossible_bound_is_dropped_and_the_possible_one_kept():
    """The top is a real limit and survives; only the floor is refused."""
    _fig, ax = _fraction_figure(y_log=True, lo="0", hi="1")
    lo, hi = ax.get_ylim()
    assert hi == pytest.approx(1.0)
    assert lo > 0.0, f"a non-positive floor reached a log axis: {lo}"


def test_the_same_range_is_honoured_exactly_on_a_linear_axis():
    """0 is a perfectly good floor when the axis is not logarithmic — the fix
    must not take a working setting away from the commoner case."""
    _fig, ax = _fraction_figure(y_log=False, lo="0", hi="1")
    assert ax.get_ylim() == pytest.approx((0.0, 1.0))


def test_a_positive_floor_is_honoured_exactly():
    """What the user should type instead, and it must be taken literally."""
    _fig, ax = _fraction_figure(y_log=True, lo="0.01", hi="1")
    assert ax.get_ylim() == pytest.approx((0.01, 1.0))


def test_the_refusal_is_said_rather_than_silent(caplog):
    """A dropped bound is a substitution: the number stays in the Properties
    field while the plot is drawn to a different range, and nothing on screen
    tells the two apart. So it is named, with what to do instead."""
    with caplog.at_level(logging.WARNING, logger="review"):
        _fraction_figure(y_log=True, lo="0", hi="1")
    said = [r.getMessage() for r in caplog.records
            if "logarithmic axis can take" in r.getMessage()]
    assert said, "the bound was dropped silently"
    assert "y min" in said[0] and "log scale off" in said[0], said[0]


def test_nothing_is_said_when_every_bound_is_usable(caplog):
    """Otherwise every log figure in the app would log on every redraw."""
    with caplog.at_level(logging.WARNING, logger="review"):
        _fraction_figure(y_log=True, lo="0.01", hi="1")
    assert not [r for r in caplog.records
                if "logarithmic axis can take" in r.getMessage()]


@pytest.mark.parametrize("scale,lo,hi,expect", [
    ("log", 0.0, 1.0, (None, 1.0)),
    ("log", -5.0, 1.0, (None, 1.0)),
    ("log", 0.5, 1.0, (0.5, 1.0)),
    ("log", 0.5, 0.0, (0.5, None)),
    ("linear", 0.0, 1.0, (0.0, 1.0)),
    ("linear", -5.0, 1.0, (-5.0, 1.0)),
])
def test_which_bounds_a_scale_can_take(scale, lo, hi, expect):
    """The rule itself, as values — a negative bound is refused for the same
    reason as zero, and a linear axis takes both."""
    from review.core.plot_style import limits_a_log_axis_can_take

    out_lo, out_hi, dropped = limits_a_log_axis_can_take(scale, lo, hi)
    assert (out_lo, out_hi) == expect
    assert bool(dropped) is ((out_lo, out_hi) != (lo, hi))
