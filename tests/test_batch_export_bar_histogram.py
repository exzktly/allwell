"""Batch Export's Bar panel offers Histogram beside Bars, Beeswarm and Violin, with its own bin count.

Through the panel's real widgets and real figure builder, in the minimal shell on the mini dataset, read off the
figure (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def panel(qtbot, mini_dataset):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.barplot.collect import bar_well_display_label
    from review.features.barplot.controller import ordered_bar_keys
    from review.features.barplot.tab_view import build_bar_plots_tab
    from review.features.batch_export import BarBatchExportPanel
    from tests._minimal_shell import load_dataset, minimal_shell
    from widgets.control_band import ControlBand

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset)
    frame = QWidget()
    QHBoxLayout(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_bar_plots_tab(ctx, frame)        # the panel reads the tab's timepoint combo, as in the app
    ctx.runtime.barplot.current_keys = lambda: ordered_bar_keys(ctx)
    ctx.runtime.barplot.well_display_label = lambda label: bar_well_display_label(ctx, label)
    p = BarBatchExportPanel(ctx, None, use_sidebar_groups=False)
    p._keep_alive = frame
    p._export_val_col_for = lambda _cb: "gfp_mean_intensity"
    return p


def test_choosing_histogram_hands_the_bin_count_its_control(panel):
    assert not panel._hist_bins_spin.isEnabled()
    panel._mode_hist_rb.setChecked(True)
    assert panel._hist_bins_spin.isEnabled() and not panel._violin_smooth_slider.isEnabled()
    panel._hist_bins_spin.setValue(4)
    assert panel._display_choice() == ("histogram", 1.0, 4)
    panel._mode_violin_rb.setChecked(True)
    assert not panel._hist_bins_spin.isEnabled()
    assert panel._display_choice()[0] == "violin"


def test_the_figure_is_drawn_as_histograms_at_the_panel_s_bin_count(panel):
    from review.domain.batch_models import BarGroup
    from review.features.barplot.renderer import HISTOGRAM_GID, HISTOGRAM_NOTE_GID

    wells = sorted(panel._ctx.state.dataset.well_paths)[:2]
    grp = BarGroup("G", members=[], solo_wells=wells)
    with panel._app_group_scope(grp), panel._app_val_col_scope("gfp_mean_intensity"):
        fig = panel._render_bar_group_figure(grp, 0.0, "0", 0.0, True, "SEM", display_mode="histogram", hist_bins=4)
    ax_mean = fig.axes[0]
    assert any(p.get_gid() == HISTOGRAM_GID for p in ax_mean.patches)
    notes = [t.get_text() for t in ax_mean.texts if t.get_gid() == HISTOGRAM_NOTE_GID]
    assert len(notes) == 1 and notes[0].startswith("4 bins ·"), notes
    assert "(histogram, 4 bins)" in ax_mean.get_title()


def test_every_render_call_passes_the_bin_count():
    """Left out of one call, a run draws 30 bins whatever the panel says, and nothing else notices."""
    from tests._repo_scan import parse_source

    tree = parse_source(ROOT / "review/features/batch_export/bar_panel.py")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("_render_bar_group_figure", "_render_per_cell_panels")]
    assert len(calls) == 2
    missing = [n.lineno for n in calls if "hist_bins" not in {k.arg for k in n.keywords}]
    assert not missing, f"render call(s) without hist_bins at line(s) {missing}"
