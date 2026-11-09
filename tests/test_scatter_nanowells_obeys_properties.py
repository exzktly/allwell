"""The Scatter Plot under Observations: Nanowells obeys the Properties panel it opens.

Reported: with the Properties sidebar open on the nanowell scatter, X log, Y log and the log bases changed nothing
(“possibly others”). The nanowell points are drawn by ``scatter/_observations.redraw``, a second renderer for the
same figure, and it read none of the figure's own preferences: the log toggles write ``scatter_x_log`` /
``scatter_y_log`` and ask for a redraw, and the redraw drew whatever log state the last *cell* draw had pinned on
the axes. The base was ignored on every scatter axis, because the scatter's log(x+1) axis was built in decades
whatever the base said.

Driven through the panel's own controls (CLAUDE.md §7.6) on the real Scatter builder in a minimal shell, asserting
what the axes draw (§7.4 #1): the scale, and the tick a base-2 axis has that a base-10 one does not (§7.8 #2).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.domain.observation import NANOWELLS  # noqa: E402


@pytest.fixture
def scatter(qtbot, tmp_path):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.scatter.controller import update_scatter_menus
    from review.features.scatter.tab_view import build_scatter_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset
    from widgets.control_band import ControlBand

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_scatter_tab(ctx, frame)
    update_scatter_menus(ctx)
    ctx.state.selection.selected_wells = {"A01", "B01"}
    ctx.state.plot.observations = NANOWELLS
    return ctx, window


def _open_panel(ctx, window, page: str):
    from review.features.export.style import launch_export_editor
    from review.features.scatter.controller import redraw_scatter_agg_orchestrate, redraw_scatter_orchestrate

    (redraw_scatter_orchestrate if page == "cells" else redraw_scatter_agg_orchestrate)(ctx)
    fig_name, canvas_name = (("_scatter_fig", "_scatter_canvas") if page == "cells"
                             else ("_scatter_agg_fig", "_scatter_agg_canvas"))
    fig, canvas = ctx.widgets.get(fig_name), ctx.widgets.get(canvas_name)
    session = launch_export_editor(window, fig, "scatter", canvas=canvas)
    assert session is not None
    return session.sidebar


def _toggle(panel, text: str):
    from widgets.toggle_switch import ToggleSwitch
    return next(t for t in panel.findChildren(ToggleSwitch) if t.text() == text)


def _base_boxes(panel):
    from PySide6.QtWidgets import QComboBox
    return [c for c in panel.findChildren(QComboBox) if c.isEditable()
            and "log axis" in (c.toolTip() or "")]


def _ax(ctx, page):
    return ctx.widgets.get("_ax_scatter" if page == "cells" else "_ax_scatter_agg")


@pytest.mark.parametrize("page", ["cells", "aggregate"])
def test_the_log_toggles_change_the_nanowell_scatter_axes(scatter, page):
    ctx, window = scatter
    panel = _open_panel(ctx, window, page)
    ax = _ax(ctx, page)
    for text, scale_of in (("X log", ax.get_xscale), ("Y log", ax.get_yscale)):
        toggle = _toggle(panel, text)
        for on in (not toggle.isChecked(), not toggle.isChecked() ^ True):
            toggle.setChecked(on)
            ax = _ax(ctx, page)
            scale_of = ax.get_xscale if text == "X log" else ax.get_yscale
            assert (scale_of() != "linear") is on, f"{page}: {text} {'on' if on else 'off'} drew {scale_of()}"


def test_the_cells_page_says_on_the_axis_that_it_is_log_plus_one(scatter):
    ctx, window = scatter
    panel = _open_panel(ctx, window, "cells")
    x = _toggle(panel, "X log")
    x.setChecked(True)
    assert _ax(ctx, "cells").get_xlabel().endswith("(log₁₀, +1)")
    x.setChecked(False)
    assert "log" not in _ax(ctx, "cells").get_xlabel()


@pytest.mark.parametrize("page", ["cells", "aggregate"])
def test_the_log_base_reaches_the_nanowell_scatter_axis(scatter, page):
    ctx, window = scatter
    panel = _open_panel(ctx, window, page)
    _toggle(panel, "X log").setChecked(True)
    x_base = _base_boxes(panel)[0]
    x_base.setCurrentText("2")
    ax = _ax(ctx, page)
    ax.figure.canvas.draw()
    ticks = list(ax.xaxis.get_major_locator()())
    # 8 is a power of two and of no other base the panel offers; base 10 never puts a major tick there.
    assert any(abs(t - 8) < 1e-9 for t in ticks), f"{page}: base 2 drew ticks {ticks}"
    x_base.setCurrentText("10")
    ax = _ax(ctx, page)
    ax.figure.canvas.draw()
    assert not any(abs(t - 8) < 1e-9 for t in ax.xaxis.get_major_locator()())


def test_the_nanowell_points_take_the_marker_size(scatter):
    from review.core.plot_style import figure_prefs

    ctx, window = scatter
    panel = _open_panel(ctx, window, "cells")
    fig = ctx.widgets.get("_scatter_fig")
    figure_prefs(ctx, fig)["marker_size"] = 9.0
    panel._redraw_bound_figure()
    sizes = {float(s) for c in _ax(ctx, "cells").collections if len(c.get_offsets()) for s in c.get_sizes()}
    assert sizes == {81.0}


def test_density_and_error_bars_are_not_offered_on_nanowells_and_come_back_on_cells(scatter):
    from review.core import plot_style
    from review.domain.observation import CELLS
    from review.features.scatter._style_facets import style_facets

    ctx, window = scatter
    facets = style_facets()
    for page, fig_name, facet in (("cells", "_scatter_fig", "scatter_density"),
                                  ("aggregate", "_scatter_agg_fig", "error_bars")):
        _open_panel(ctx, window, page)
        fig = ctx.widgets.get(fig_name)
        assert facet not in facets[fig_name].live(getattr(fig, "_aw_style_view", "")), (page, facet)
        ctx.state.plot.observations = CELLS
        _open_panel(ctx, window, page)
        assert facet in facets[fig_name].live(getattr(fig, "_aw_style_view", "")), (page, facet)
        ctx.state.plot.observations = NANOWELLS
    assert plot_style  # imported for the side effect of nothing; keeps the module in this test's import graph
