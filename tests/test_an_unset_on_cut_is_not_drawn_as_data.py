"""An ON cutoff nobody set, which cuts nothing, is said rather than drawn (NANOWELL_OBSERVATIONS_TRACKER #3).

The segmentation channel's default ThreshFracOn is 0.0, so "Fraction of Cells Above Threshold" is 1.0 and
"Events above threshold (N)" is the cell count for every well — bars that look like a measurement. A nanowell run
showed it on its first plot (its segmentation channel is the phase channel). The panels now carry a sentence
instead; a cutoff the user stored, including a deliberate 0, is drawn as data.

Driven through the real Bar Plots builder and redraw in a minimal shell (CLAUDE.md §7.8 #3), on the synthetic
nanowell dataset. The assertions read the figure, not the predicate (§7.4 #1).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.core.threshold_note import ON_CUT_NOT_SET_NOTE  # noqa: E402


@pytest.fixture
def bar_ctx(qtbot, tmp_path):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.features.barplot.tab_view import build_bar_plots_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    # The Plotting page's shared band, as centre_view registers it: a leaf's controls live in its slots, and a
    # free-standing fallback band nothing holds would be collected with every control in it.
    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from widgets.control_band import ControlBand
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_bar_plots_tab(ctx, frame)
    ctx.state.selection.selected_wells = {"A01", "B01"}
    from review.features.barplot.controller import ordered_bar_keys, update_bar_tp_menu
    from review.features.barplot.collect import bar_well_display_label
    # The two runtime handles ReviewApp wires (review/runtime_app.py), wired the same way.
    ctx.runtime.barplot.current_keys = lambda: ordered_bar_keys(ctx)
    ctx.runtime.barplot.well_display_label = lambda label: bar_well_display_label(ctx, label)
    update_bar_tp_menu(ctx)               # what the tab's activation does: the timepoint popup's choices
    return ctx


def _draw(ctx, channel: str):
    from review.features.barplot.renderer import redraw_bars

    ctx.state.channel.active_channel = channel
    ctx.state.channel.active_val_col = f"{channel}_mean_intensity"
    redraw_bars(ctx)
    return ctx.widgets.get("_ax_bar_frac"), ctx.widgets.get_or_none("_ax_bar_n")


def _notes(ax) -> list[str]:
    return [t.get_text() for t in ax.texts if t.get_gid() == "on_cut_not_set"]


def test_the_segmentation_channel_with_no_cut_draws_the_sentence_not_bars(bar_ctx):
    ax_frac, ax_n = _draw(bar_ctx, "phase")
    assert _notes(ax_frac) == [ON_CUT_NOT_SET_NOTE]
    assert len(ax_frac.patches) == 0 and len(ax_frac.lines) == 0
    if ax_n is not None:
        assert _notes(ax_n) == [ON_CUT_NOT_SET_NOTE] and len(ax_n.patches) == 0


def test_a_stored_zero_is_a_choice_and_is_drawn(bar_ctx):
    bar_ctx.state.cell_gating.thresh_frac_on["phase"] = 0.0
    ax_frac, _ax_n = _draw(bar_ctx, "phase")
    assert _notes(ax_frac) == []
    assert len(ax_frac.patches) >= 2                     # a bar per selected well


def test_a_readout_channel_s_default_cut_is_a_real_cut_and_is_drawn(bar_ctx):
    ax_frac, _ax_n = _draw(bar_ctx, "gfp")
    assert _notes(ax_frac) == []
    assert len(ax_frac.patches) >= 2


@pytest.fixture
def line_ctx(qtbot, tmp_path):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.features.lineplot.tab_view import build_line_graphs_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw_line")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    # The Plotting page's shared band, as centre_view registers it: a leaf's controls live in its slots, and a
    # free-standing fallback band nothing holds would be collected with every control in it.
    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from widgets.control_band import ControlBand
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_line_graphs_tab(ctx, frame)
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _draw_lines(ctx, channel: str):
    from review.core.plot_orchestrator import redraw_app

    ctx.state.channel.active_channel = channel
    ctx.state.channel.active_val_col = f"{channel}_mean_intensity"
    redraw_app(ctx)                      # the fan-out that hands the Line tab its figure inputs
    return ctx.widgets.get("_line_ax_frac"), ctx.widgets.get("_line_ax_n")


def test_the_line_tab_says_it_too_and_draws_a_stored_zero(line_ctx):
    ax_frac, ax_n = _draw_lines(line_ctx, "phase")
    assert _notes(ax_frac) == [ON_CUT_NOT_SET_NOTE] and _notes(ax_n) == [ON_CUT_NOT_SET_NOTE]
    assert len(ax_frac.lines) == 0 and len(ax_n.lines) == 0
    line_ctx.state.cell_gating.thresh_frac_on["phase"] = 0.0
    ax_frac, _ax_n = _draw_lines(line_ctx, "phase")
    assert _notes(ax_frac) == [] and len(ax_frac.lines) >= 2


def _footers(fig) -> list[str]:
    return [t.get_text() for t in fig.texts if t.get_gid() == "observations_footer"]


def test_a_nanowell_plate_s_cell_plots_say_their_cells_are_pooled_from_occupied_nanowells(bar_ctx, line_ctx):
    _draw(bar_ctx, "gfp")
    assert _footers(bar_ctx.runtime.barplot.fig) == [
        "Observations: cells · pooled from 3–4 occupied nanowells per well"]
    _draw(bar_ctx, "gfp")                                   # a redraw writes one line, not two
    assert len(_footers(bar_ctx.runtime.barplot.fig)) == 1
    _draw_lines(line_ctx, "gfp")
    assert _footers(line_ctx.widgets.get("_line_fig")) == [
        "Observations: cells · pooled from 3–4 occupied nanowells per well"]


def test_without_the_nanowells_feature_the_footer_says_the_views_are_not_in_this_build(bar_ctx, monkeypatch):
    monkeypatch.setattr(bar_ctx.features, "service", lambda key: None)
    _draw(bar_ctx, "gfp")
    assert _footers(bar_ctx.runtime.barplot.fig) == [
        "Observations: cells · pooled from 3–4 occupied nanowells per well · nanowell views are not in this build"]
