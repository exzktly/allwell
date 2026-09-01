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

from well_viewer.core.plot_style import (
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
    from well_viewer.core import plot_style as ps

    fig, ax, prefs = _fig_with_limits(y_lim_min="1", y_lim_max="1000")

    real = ps.apply_log_minor_ticks

    def _autoscaling(axes, which, base):
        axes.autoscale(enable=True, axis="y")
        axes.set_ylim(auto=True)
        axes.autoscale_view()
        return real(axes, which, base)

    monkeypatch.setattr(ps, "apply_log_minor_ticks", _autoscaling)
    prefs["y_log"] = True
    with caplog.at_level(logging.WARNING, logger="well_viewer"):
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
    with caplog.at_level(logging.WARNING, logger="well_viewer"):
        apply_export_style_prefs(fig, prefs)
    assert not [r for r in caplog.records if "limits" in r.getMessage()]


def test_one_typed_bound_holds_and_the_other_stays_free():
    """A user who pins only the top keeps an autoscaled floor — the hold must
    not turn a half-specified range into a fully pinned one."""
    fig, ax, prefs = _fig_with_limits(y_lim_max="1000")
    apply_export_style_prefs(fig, prefs)
    assert ax.get_ylim()[1] == pytest.approx(1000.0)
    assert ax.get_ylim()[0] < 1000.0
