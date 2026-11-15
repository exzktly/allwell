"""Distribution, logarithmic x axis: log-spaced bins, and a count of the values the axis cannot show.

A background-subtracted intensity is at or below zero for a fair share of cells. On a log x axis those values have no
place, and the tab used to drop them without a word; it also fell back to *linear* bin edges whenever the minimum was
not positive, so the bars drawn on the log axis were of wildly unequal width.

The fixture's values are chosen so the two rules differ in every edge: log-spaced edges over the positive values
1 … 1000 are powers of ten, and linear edges from the minimum start at −5.
"""

from __future__ import annotations

import numpy as np
import pytest

from review.features.distribution import controller as C
from review.features.distribution._pooled import ExactStats

VALUES = np.array([-5.0, 0.0, 1.0, 10.0, 100.0, 1000.0])
NOTE = "2 of 6 values (33.3%) are at or below zero and are not on the logarithmic axis"


def test_log_x_bins_are_log_spaced_over_the_positive_values():
    edges = C._shared_bin_edges([VALUES], 3, True)
    assert edges == pytest.approx([1.0, 10.0, 100.0, 1000.0])
    assert C._grid_for([VALUES], n_pts=4, log_x=True) == pytest.approx([1.0, 10.0, 100.0, 1000.0])


def test_a_linear_axis_spans_every_value():
    assert C._shared_bin_edges([VALUES], 3, False) == pytest.approx([-5.0, 330.0, 665.0, 1000.0])


def test_all_positive_values_are_log_spaced_from_their_minimum():
    assert C._shared_bin_edges([VALUES[2:]], 3, True) == pytest.approx([1.0, 10.0, 100.0, 1000.0])


def test_with_no_positive_value_the_edges_stay_linear():
    assert C._shared_bin_edges([np.array([-4.0, -2.0, 0.0])], 2, True) == pytest.approx([-4.0, -2.0, 0.0])


def test_the_note_counts_the_values_at_or_below_zero_across_groups():
    groups = [("A", "k", VALUES[:4]), ("B", "r", VALUES[4:])]
    assert C.non_positive_note(groups, None, True) == NOTE
    assert C.non_positive_note(groups, None, False) == ""
    assert C.non_positive_note([("B", "r", VALUES[2:])], None, True) == ""


def test_a_sampled_group_is_counted_over_every_value():
    every = np.r_[np.full(300, -1.0), np.full(700, 5.0)]
    sample = np.full(10, 5.0)                               # the sample happens to hold no value at or below zero
    exact = [ExactStats(n=1000, lo=-1.0, hi=5.0, median=5.0, hist=lambda edges: np.histogram(every, edges)[0])]
    assert C.non_positive_count([("A", "k", sample)], exact) == (300, 1000)
    assert C.non_positive_note([("A", "k", sample)], exact, True) == (
        "300 of 1,000 values (30.0%) are at or below zero and are not on the logarithmic axis")


# ── the figure the tab draws ────────────────────────────────────────────────────────────────────────────────────

pytest.importorskip("PySide6")


@pytest.fixture
def ctx(qtbot):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.distribution.tab_view import build_distribution_tab
    from tests._minimal_shell import minimal_shell
    from widgets.control_band import ControlBand

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_distribution_tab(ctx, frame)
    return ctx


def _draw(ctx, mode: str, log_x: bool, layout: str = "Overlay"):
    groups = [("A", "#3366cc", VALUES[:4]), ("B", "#cc3366", VALUES[4:])]
    C._draw_distribution(ctx, ctx.widgets.get("_distribution_canvas"), groups, mode, 3, log_x, False,
                         float("nan"), layout, None)
    fig = ctx.widgets.get("_distribution_fig")
    notes = [t.get_text() for ax in fig.axes for t in ax.texts if getattr(t, C.NON_POSITIVE_NOTE_TAG, False)]
    return fig, notes


@pytest.mark.parametrize("mode, layout", [("Histogram", "Overlay"), ("Histogram + KDE", "Faceted (one per group)"),
                                          ("KDE only", "Overlay"), ("CDF", "Overlay")])
def test_a_log_x_figure_says_how_many_values_it_leaves_off(ctx, mode, layout):
    fig, notes = _draw(ctx, mode, True, layout)
    assert notes == [NOTE]
    if mode.startswith("Histogram"):
        lefts = sorted({round(float(p.get_x()), 6) for ax in fig.axes for p in ax.patches})
        assert lefts == [1.0, 10.0, 100.0]                  # the bars stand on the log-spaced edges


def test_a_linear_axis_and_a_per_group_view_carry_no_such_note(ctx):
    assert _draw(ctx, "Histogram", False)[1] == []
    assert _draw(ctx, "Violin (per group)", True)[1] == []  # the value is on y there; Log X does not apply
