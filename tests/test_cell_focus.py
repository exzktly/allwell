"""One focused cell across the Review surfaces (tracker F1–F6).

The focused cell is set by whichever surface names a cell and drawn by every
surface that can: the status-bar chip, a ring on the per-cell Scatter, and the
outline in Segmentation. These drive the real state, bus and widgets.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.core.cell_focus import (  # noqa: E402
    focus_cell, focused_cell, focused_nucleus_in, same_frame, set_focused_cell,
)
from review.core.focus_state import FocusedCell  # noqa: E402
from tests.test_scatter_draws_nanowell_observations import scatter_ctx  # noqa: E402,F401


@pytest.fixture
def ctx(qtbot):
    from tests._minimal_shell import minimal_shell

    ctx, _window = minimal_shell(qtbot)
    return ctx


def test_focusing_a_cell_emits_once_and_clearing_emits_again(ctx):
    seen = []
    ctx.bus.focused_cell_changed.connect(lambda state: seen.append(state.cell))
    focus_cell(ctx, "B03", "F001", "24h", "187.0")
    focus_cell(ctx, "B03", "F001", "24h", 187)            # the same cell: no second event
    set_focused_cell(ctx, None)
    assert seen == [FocusedCell("B03", "F001", "24h", 187), None]


def test_a_field_matches_however_its_tokens_are_spelled():
    cell = FocusedCell("B03", "F001", "24h", 187)
    assert same_frame(cell, "B03", "F001", "24")
    assert not same_frame(cell, "B03", "F002", "24h")
    assert not same_frame(cell, "B04", "F001", "24h")


def test_segmentation_outlines_the_focused_cell_only_in_its_own_field(ctx):
    focus_cell(ctx, "A01", "F001", "0h", 2)
    assert focused_nucleus_in(ctx, "A01", "F001", "0h") == 2
    assert focused_nucleus_in(ctx, "A01", "F002", "0h") is None   # same number, another field


def test_the_chip_names_the_cell_and_its_cross_clears_it(ctx, qtbot):
    from review.core.focus_view import build_focus_chip

    win = SimpleNamespace(_ctx=ctx, _widget_registry=ctx.widgets)
    chip = build_focus_chip(win, ctx.window)
    assert chip.isHidden()
    focus_cell(ctx, "A01", "F001", "0h", 2)
    assert chip.text() == "A01 · F001 · 0h · #2" and not chip.isHidden()
    chip._close.click()
    assert focused_cell(ctx) is None and chip.isHidden()
    focus_cell(ctx, "A01", "F001", "0h", 2)
    ctx.bus.dataset_loaded.emit(ctx.state.dataset)       # a new dataset: the old cell is gone
    assert focused_cell(ctx) is None


def test_clicking_the_chip_opens_the_cell_in_segmentation(ctx, monkeypatch):
    from review.core.focus_view import build_focus_chip

    calls = []
    monkeypatch.setattr(ctx.features, "query",
                        lambda key, *a, default=None, **k: calls.append((key, a[1:])) or True)
    chip = build_focus_chip(SimpleNamespace(_ctx=ctx, _widget_registry=ctx.widgets), ctx.window)
    focus_cell(ctx, "A01", "F001", "0h", 2)
    chip._button.click()
    assert calls == [("image_browser.show_cell", ("A01", "F001", "0h", "2"))]


def _ring_points(ax):
    from review.features.scatter._focus_ring import RING_GID
    return [tuple(map(float, (ln.get_xdata()[0], ln.get_ydata()[0])))
            for ln in ax.lines if ln.get_gid() == RING_GID]


def test_the_scatter_rings_the_focused_cell_and_follows_the_focus(scatter_ctx):
    from review.data.data_loading import get_rows
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx = scatter_ctx
    ctx.widgets.get("_scatter_ch_x_cb").setCurrentText("GFP")
    ctx.widgets.get("_scatter_ch_y_cb").setCurrentText("GFP")
    redraw_scatter_orchestrate(ctx)
    ax = ctx.widgets.get("_ax_scatter")
    pts = ctx.runtime.scatter.interaction_cache["axes_points"][0][1]
    assert len(pts) > 2 and _ring_points(ax) == []

    def focus_point(i):
        x, y, (well, _f, nid, row) = pts[i]
        r = get_rows(ctx, well).iloc[row]
        focus_cell(ctx, well, r["fov"], r["timepoint"], nid)
        return (float(x), float(y))

    first = focus_point(0)
    assert _ring_points(ax) == [first]                    # painted on the focus event, no redraw
    ax.figure.canvas.draw()                               # and drawable: its colour is one matplotlib reads
    second = focus_point(len(pts) - 1)
    assert _ring_points(ax) == [second] and second != first
    redraw_scatter_orchestrate(ctx)                       # a redraw keeps it
    assert _ring_points(ctx.widgets.get("_ax_scatter")) == [second]
    set_focused_cell(ctx, None)
    assert _ring_points(ctx.widgets.get("_ax_scatter")) == []


def test_a_cell_focused_elsewhere_with_the_same_number_is_not_ringed(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx = scatter_ctx
    redraw_scatter_orchestrate(ctx)
    _x, _y, (well, _f, nid, _row) = ctx.runtime.scatter.interaction_cache["axes_points"][0][1][0]
    focus_cell(ctx, well, "F999", "t_never", nid)          # same well and id, another field
    assert _ring_points(ctx.widgets.get("_ax_scatter")) == []
