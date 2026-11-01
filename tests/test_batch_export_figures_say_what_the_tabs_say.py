"""Batch Export's Bar and Line figures say what the tabs they mirror say (NANOWELL_OBSERVATIONS_TRACKER #3, #18).

An exported figure is the one most likely to leave the app, so the two sentences the tabs carry must survive the
export: an ON cutoff nobody set that cuts nothing draws a sentence in place of the Fraction / Events panels, not
1.0 bars; and a nanowell plate's cells figure ends "Observations: cells · pooled from N occupied nanowells per
well". Through the panels' real figure builders, in a minimal shell, on the synthetic nanowell dataset; read off
the figure (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.core.threshold_note import ON_CUT_NOT_SET_NOTE  # noqa: E402

FOOTER = "Observations: cells · pooled from 3–4 occupied nanowells per well"


@pytest.fixture
def nw_ctx(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    return ctx


def _group():
    from review.domain.batch_models import BarGroup

    return BarGroup("G", members=[], solo_wells=["A01", "B01"])


def _notes(ax):
    return [t.get_text() for t in ax.texts if t.get_gid() == "on_cut_not_set"]


def _footers(fig):
    return [t.get_text() for t in fig.texts if t.get_gid() == "observations_footer"]


def _line_figure(ctx, channel):
    from review.features.batch_export import BatchExportPanel

    panel = BatchExportPanel(ctx, None, use_sidebar_groups=False)
    grp = _group()
    with panel._app_group_scope(grp), panel._app_val_col_scope(f"{channel}_mean_intensity"):
        return panel._render_group_figure(grp, 0.0, True, "SEM", val_col=f"{channel}_mean_intensity")


def _bar_figure(ctx, channel):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.barplot.collect import bar_well_display_label
    from review.features.barplot.controller import ordered_bar_keys
    from review.features.barplot.tab_view import build_bar_plots_tab
    from review.features.batch_export import BarBatchExportPanel
    from widgets.control_band import ControlBand

    # The Bar panel reads the Bar Plots tab's timepoint combo, as it does in the app, so the tab is built first.
    frame = QWidget()
    QHBoxLayout(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_bar_plots_tab(ctx, frame)
    ctx.runtime.barplot.current_keys = lambda: ordered_bar_keys(ctx)
    ctx.runtime.barplot.well_display_label = lambda label: bar_well_display_label(ctx, label)
    panel = BarBatchExportPanel(ctx, None, use_sidebar_groups=False)
    panel._keep_alive = frame
    panel._export_val_col_for = lambda _cb: f"{channel}_mean_intensity"     # the panel's channel picker
    grp = _group()
    with panel._app_group_scope(grp), panel._app_val_col_scope(f"{channel}_mean_intensity"):
        return panel._render_bar_group_figure(grp, 24.0, "24.0", 0.0, True, "SEM")


@pytest.mark.parametrize("figure", [_line_figure, _bar_figure], ids=["line", "bar"])
def test_an_unset_cut_on_the_segmentation_channel_is_said_on_the_exported_figure(nw_ctx, figure):
    fig = figure(nw_ctx, "phase")
    frac, n = fig.axes[1], fig.axes[2]
    assert _notes(frac) == [ON_CUT_NOT_SET_NOTE] and _notes(n) == [ON_CUT_NOT_SET_NOTE]
    assert len(frac.patches) == 0 and len(frac.lines) == 0
    assert _footers(fig) == [FOOTER]


@pytest.mark.parametrize("figure", [_line_figure, _bar_figure], ids=["line", "bar"])
def test_a_stored_cut_draws_and_the_footer_stays(nw_ctx, figure):
    nw_ctx.state.cell_gating.thresh_frac_on["phase"] = 0.0
    fig = figure(nw_ctx, "phase")
    assert _notes(fig.axes[1]) == []
    assert _footers(fig) == [FOOTER]


def test_a_standard_plate_s_exported_figure_carries_no_footer(qtbot, mini_dataset):
    from review.domain.batch_models import BarGroup
    from review.features.batch_export import BatchExportPanel
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset)
    panel = BatchExportPanel(ctx, None, use_sidebar_groups=False)
    wells = sorted(ctx.state.dataset.well_paths)[:2]
    grp = BarGroup("G", members=[], solo_wells=wells)
    with panel._app_group_scope(grp), panel._app_val_col_scope("gfp_mean_intensity"):
        fig = panel._render_group_figure(grp, 50.0, True, "SEM", val_col="gfp_mean_intensity")
    assert _footers(fig) == []
