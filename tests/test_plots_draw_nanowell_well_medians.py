"""Bar, Line and Heat Map under Observations: Nanowells draw each well's median over its nanowells, and wells are
the replicates (NANOWELL_OBSERVATIONS_TRACKER #13, #14, #15).

The fixture is chosen so the plausible wrong answers differ (CLAUDE.md §7.8 #2). At 24 h A01's five nanowells hold
3, 2, 0, 4, 5 cells and B01's 2, 4, 1, 0, 0: the well medians are 3 and 1, the well means 2.8 and 1.4, and the
pooled cell counts 14 and 7 — a bar that drew any of the others fails on the exact height. Through the real Bar and
Line builders in a minimal shell, reading the drawn artists (§7.4 #1).
"""

from __future__ import annotations

import types

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.domain.observation import NANOWELLS  # noqa: E402


def _shell(qtbot, tmp_path, builder_path: str, name: str):
    import importlib

    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset
    from widgets.control_band import ControlBand

    root = build_nanowell_dataset(tmp_path / name)
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    module, fn = builder_path.rsplit(".", 1)
    getattr(importlib.import_module(module), fn)(ctx, frame)
    ctx.state.selection.selected_wells = {"A01", "B01"}
    ctx.state.plot.observations = NANOWELLS
    ctx.state.plot.nanowell_metric = "n"
    return ctx


def _footer(fig) -> str:
    return next(t.get_text() for t in fig.texts if t.get_gid() == "observations_footer")


def test_bars_are_well_medians_and_a_lone_well_has_no_error_bar(qtbot, tmp_path):
    from review.features.barplot.collect import bar_well_display_label
    from review.features.barplot.controller import ordered_bar_keys, update_bar_tp_menu
    from review.features.barplot.renderer import redraw_bars

    ctx = _shell(qtbot, tmp_path, "review.features.barplot.tab_view.build_bar_plots_tab", "bar")
    ctx.runtime.barplot.current_keys = lambda: ordered_bar_keys(ctx)
    ctx.runtime.barplot.well_display_label = lambda label: bar_well_display_label(ctx, label)
    update_bar_tp_menu(ctx)
    ctx.state.bar.tp_selections = {k: float(k) == 24.0 for k in ctx.state.bar.tp_selections}
    redraw_bars(ctx)
    ax = ctx.widgets.get("_ax_bar_mean")
    heights = [p.get_height() for p in ax.patches]
    assert heights == [3.0, 1.0]                                     # A01, B01: medians, not 2.8 / 1.4, not 14 / 7
    assert not ax.containers or all(type(c).__name__ != "ErrorbarContainer" for c in ax.containers)
    occupied = [p.get_height() for p in ctx.widgets.get("_ax_bar_frac").patches]
    # The share of each well's nanowells holding a cell: 4 of 5 and 3 of 5 — not the median of the 0/1 flags (1, 1).
    assert np.allclose(occupied, [0.8, 0.6])
    assert ax.get_title().startswith("Cell count — well median over nanowells, mean ± SEM over wells")
    assert _footer(ctx.runtime.barplot.fig).startswith("Observations: nanowells · 10 of 10 used")


def test_lines_follow_each_well_s_median_over_time(qtbot, tmp_path):
    from review.core.plot_orchestrator import redraw_app

    ctx = _shell(qtbot, tmp_path, "review.features.lineplot.tab_view.build_line_graphs_tab", "line")
    redraw_app(ctx)
    ax = ctx.widgets.get("_line_ax_mean")
    by_label = {ln.get_label(): ln.get_ydata().tolist() for ln in ax.lines}
    assert by_label == {"A01": [1.0, 3.0], "B01": [1.0, 1.0]}
    assert ax.get_title().startswith("Cell count — well median over nanowells")
    assert _footer(ctx.widgets.get("_line_fig")).startswith("Observations: nanowells · 20 of 20 used")


def test_a_heat_map_cell_is_the_mean_over_its_wells_of_their_medians(qtbot, tmp_path):
    from review.features.heatmap._observations import nanowell_grid

    ctx = _shell(qtbot, tmp_path, "review.features.lineplot.tab_view.build_line_graphs_tab", "heat")
    layout = types.SimpleNamespace(rows=1, cols=3, cells={(0, 0): ["A01"], (0, 1): ["B01"], (0, 2): ["A01", "B01"]})
    grid = nanowell_grid(ctx, layout, 24.0, lambda ws: list(ws))
    assert grid.arr.tolist() == [[3.0, 1.0, 2.0]]
    assert np.isnan(grid.err[0, 0]) and np.isclose(grid.err[0, 2], 1.0)      # SEM over two wells: |3 − 1| / 2
    assert grid.label == "Cell count"
    ctx.state.plot.observations = "cells"
    assert nanowell_grid(ctx, layout, 24.0, lambda ws: list(ws)) is None


def test_a_distribution_is_of_every_nanowell_value_not_of_cells(qtbot, tmp_path):
    from review.features.distribution._observations import nanowell_groups
    from tests.fixtures.nanowell_dataset import COUNTS

    ctx = _shell(qtbot, tmp_path, "review.features.lineplot.tab_view.build_line_graphs_tab", "dist")
    groups, label, footer = nanowell_groups(ctx, 24.0)
    got = {lbl: sorted(vals.tolist()) for lbl, _c, vals in groups}
    # One value per nanowell — the zeros included, since an empty nanowell has a count of 0 — not one per cell.
    assert got == {w: sorted(float(n) for n in COUNTS[w]["24h"]) for w in ("A01", "B01")}
    assert label == "Cell count"
    assert footer.startswith("Observations: nanowells · 10 of 10 used") and "each point is one nanowell" in footer


def test_the_distribution_tab_draws_them_through_its_own_renderer(qtbot, tmp_path):
    from review.features.distribution.controller import redraw_distribution

    ctx = _shell(qtbot, tmp_path, "review.features.distribution.tab_view.build_distribution_tab", "dist_tab")
    redraw_distribution(ctx)
    fig = ctx.widgets.get("_distribution_fig")
    assert _footer(fig).startswith("Observations: nanowells")
    assert any(ax.get_xlabel() == "Cell count" for ax in fig.axes)


def test_under_cells_the_distribution_says_its_cells_are_a_nanowell_plate_s(qtbot, tmp_path):
    from review.features.distribution.controller import redraw_distribution

    ctx = _shell(qtbot, tmp_path, "review.features.distribution.tab_view.build_distribution_tab", "dist_cells")
    ctx.state.plot.observations = "cells"
    redraw_distribution(ctx)
    assert _footer(ctx.widgets.get("_distribution_fig")) == (
        "Observations: cells · pooled from 3–4 occupied nanowells per well")
