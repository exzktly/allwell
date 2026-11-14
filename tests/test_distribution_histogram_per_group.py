"""The Distribution tab's *Histogram (per group)*: each group as a vertical histogram beside the others.

The value fixture is the shared core's (``tests/test_column_histogram.py``), so per-group bins, each group scaled to
its own peak, or counts in place of probabilities put other numbers on the bars. The sampled case holds the bars to
every value, as the tab's other histograms are; the last test drives the tab's real Mode combo.
"""

from __future__ import annotations

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

from review.features.distribution import controller as C  # noqa: E402
from tests.test_distribution_draws_a_stated_sample import (  # noqa: E402,F401 — the fixture and its helpers
    _pooled_groups, _RenderCtx, _whole, ctx,
)

A = [0.5, 0.5, 0.5, 3.5]
B = [1.0, 1.0, 1.2, 1.2, 2.6, 2.6, 3.0, 3.0]


def _render(ctx, groups, bins, exact=None, log_y=False):
    ax = Figure().add_subplot()
    C._render_overlay(_RenderCtx(ctx), ax, groups, C.HISTOGRAM_PER_GROUP, bins, False, float("nan"), log_y=log_y,
                      exact=exact)
    return ax


def _bars(ax):
    """Per column x: the bars' ``[bottom, height, width, …]``, bottom up."""
    out: dict = {}
    for p in ax.patches:
        if p.get_gid() == C.HISTOGRAM_GID:
            out.setdefault(round(p.get_x() + p.get_width() / 2, 9), []).append(
                (p.get_y(), p.get_height(), p.get_width()))
    return {x: [v for bar in sorted(bars) for v in bar] for x, bars in out.items()}


def _note(ax):
    return [t.get_text() for t in ax.texts if t.get_gid() == C.HISTOGRAM_NOTE_GID]


def test_the_groups_share_edges_and_one_width_scale(ctx):
    ax = _render(ctx, [("A", "#4488cc", np.array(A)), ("B", "#cc8844", np.array(B))], 4)
    assert _bars(ax) == {
        1.0: pytest.approx([0.5, 0.75, 0.8, 2.75, 0.75, 0.8 / 3]),
        2.0: pytest.approx([0.5, 0.75, 1.6 / 3, 2.0, 0.75, 0.8 / 3, 2.75, 0.75, 0.8 / 3]),
    }
    assert _note(ax) == ["4 bins · widest bar = 75% of a group"]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["A", "B"]
    assert ax.get_ylabel() == "Value"


def test_no_threshold_line_is_drawn_across_the_columns(ctx):
    ctx.state.plot.show_threshold_line = True
    ax = _render(ctx, [("A", "#4488cc", np.array(A)), ("B", "#cc8844", np.array(B))], 4)
    assert not [ln for ln in ax.lines if ln.get_linestyle() == "--"]


def test_under_y_log_the_bins_are_log_spaced(ctx):
    ax = _render(ctx, [("A", "#4488cc", np.array([1.0, 10.0, 10.0, 100.0]))], 2, log_y=True)
    assert ax.get_yscale() == "log"
    assert sorted({round(p.get_y(), 9) for p in ax.patches if p.get_gid() == C.HISTOGRAM_GID}) == [1.0, 10.0]
    assert _note(ax) == ["2 bins (log-spaced) · widest bar = 75% of a group"]


def test_a_sampled_group_is_binned_over_every_value(ctx, monkeypatch):
    from review.domain.column_histogram import column_histograms

    whole = _whole(ctx)
    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        ax = _render(ctx, groups, 10, exact=exact)
        lo = min(float(v.min()) for _, _, v in whole)
        hi = max(float(v.max()) for _, _, v in whole)
        edges = np.linspace(lo, hi, 11)
        full = column_histograms([v for _, _, v in whole], 10)       # the same pooled range, every value
        assert full.edges == pytest.approx(edges)
        drawn = {round(p.get_x() + p.get_width() / 2, 9): [] for p in ax.patches if p.get_gid() == C.HISTOGRAM_GID}
        for p in ax.patches:
            if p.get_gid() == C.HISTOGRAM_GID:
                drawn[round(p.get_x() + p.get_width() / 2, 9)].append(round(p.get_width() / 2, 9))
        for i in range(len(whole)):
            expected = [round(w, 9) for w in full.half_widths(i, 0.4) if w > 0]
            assert sorted(drawn[float(i + 1)]) == sorted(expected)
        sample_only = column_histograms([v for _, _, v in groups], 10)
        assert not np.allclose(sample_only.half_widths(0, 0.4), full.half_widths(0, 0.4)), \
            "the sample alone gives the same bars; the test measures nothing"
    finally:
        pool.close()


def test_the_tab_offers_it_and_draws_it(qtbot, mini_dataset):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.distribution.tab_view import build_distribution_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from widgets.control_band import ControlBand

    shell, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = shell, shell.state, shell.features
    load_dataset(shell, mini_dataset)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    shell.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_distribution_tab(shell, frame)
    shell.state.selection.selected_wells = set(sorted(shell.state.dataset.well_paths)[:2])
    combo = shell.widgets.get("_distribution_mode_cb")
    combo.setCurrentText(C.HISTOGRAM_PER_GROUP)
    assert combo.currentText() == C.HISTOGRAM_PER_GROUP
    fig = shell.widgets.get("_distribution_fig")
    patches = [p for ax in fig.axes for p in ax.patches if p.get_gid() == C.HISTOGRAM_GID]
    assert patches
    notes = [t.get_text() for ax in fig.axes for t in ax.texts if t.get_gid() == C.HISTOGRAM_NOTE_GID]
    assert len(notes) == 1 and notes[0].startswith("40 bins ·"), notes      # the tab's Bins, default 40


def test_a_group_of_fewer_than_three_values_is_drawn_as_its_dots_and_the_note_counts_it(ctx):
    """``column_histogram`` gives a group of fewer than three values no bars and says the renderer draws its values
    as dots; this tab drew a median tick alone. Group C's two values are both drawn, at its column, and the note
    says one group is drawn as dots."""
    ax = _render(ctx, [("A", "#4488cc", np.array(A)), ("C", "#cc8844", np.array([1.0, 3.0]))], 4)
    dots = sorted(float(y) for c in ax.collections if c.get_gid() == C.HISTOGRAM_DOTS_GID
                  for (x, y) in c.get_offsets() if x == 2.0)
    assert dots == [1.0, 3.0]
    assert _note(ax) == ["4 bins · widest bar = 75% of a group · 1 group of fewer than 3 values drawn as dots"]


def test_under_y_log_a_value_at_or_below_zero_is_left_off_and_counted_not_a_reason_to_drop_log(ctx):
    """One value ≤ 0 in any group used to make the edges linear and drop log altogether, silently: Log Y stayed
    ticked, the bins were linear, the lowest bar started at -5, and the note counted nothing left off. The shared
    ``column_histogram`` (Bar Plots, Nanowells) drops such values from a log axis and says how many; so does this tab
    now. A holds -5 and 0, which have no place on a log axis; every other value is placed on log-spaced bins."""
    groups = [("A", "#4488cc", np.array([-5.0, 0.0, 1.0, 10.0, 100.0, 1000.0])),
              ("B", "#cc8844", np.array([2.0, 20.0, 200.0, 300.0]))]
    ax = _render(ctx, groups, 3, log_y=True)
    assert ax.get_yscale() == "log"
    assert sorted({round(p.get_y(), 6) for p in ax.patches if p.get_gid() == C.HISTOGRAM_GID}) == [1.0, 10.0, 100.0]
    assert _note(ax) == ["3 bins (log-spaced) · widest bar = 50% of a group · 2 values ≤ 0 not on the log axis"]
