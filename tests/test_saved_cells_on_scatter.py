"""A highlighted saved set on the per-cell Scatter (tracker M7).

Driven through the real Scatter builder and redraw on the shape-feature mini
dataset, where every nucleus id repeats at three timepoints: the set holds
nucleus 2 at 24 h and nucleus 5 at 0 h, so an implementation that matched on
nucleus id alone would light six points instead of two.
"""

from __future__ import annotations


import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def ctx(qtbot, mini_dataset_shape):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.scatter.controller import update_scatter_menus
    from review.features.scatter.tab_view import build_scatter_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from widgets.control_band import ControlBand

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset_shape)
    ctx.state.dataset.data_dir = mini_dataset_shape
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_scatter_tab(ctx, frame)
    update_scatter_menus(ctx)
    ctx.state.selection.selected_wells = {"A01"}
    ctx.widgets.get("_scatter_ch_x_cb").setCurrentText("GFP")
    ctx.widgets.get("_scatter_ch_y_cb").setCurrentText("MCHERRY")
    return ctx


SET_CELLS = [("A01", "1", "24h", 2), ("A01", "1", "0h", 5)]


def _redraw(ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate
    redraw_scatter_orchestrate(ctx)
    return ctx.widgets.get("_ax_scatter")


def _set_points(ax):
    from review.features.scatter._cell_rings import SET_GID
    lines = [ln for ln in ax.lines if ln.get_gid() == SET_GID]
    return sorted(zip(*(lines[0].get_data()))) if lines else []


def _note(ax):
    from review.features.scatter._cell_rings import NOTE_GID
    return [t.get_text() for t in ax.texts if t.get_gid() == NOTE_GID]


def _highlight(ctx, cells=SET_CELLS):
    from review.core.cell_sets import add_set, set_highlighted
    s = add_set(ctx, cells, source="test")
    set_highlighted(ctx, s.name)
    return s


def test_the_highlighted_set_s_cells_and_only_they_are_drawn_solid(ctx):
    _highlight(ctx)
    ax = _redraw(ctx)
    # GFP / mCherry of nucleus 2 at 24 h and nucleus 5 at 0 h (the fixture's rows 7 and 4).
    assert [tuple(map(float, p)) for p in _set_points(ax)] == [(105.0, 52.5), (150.0, 75.0)]
    assert _note(ax) == ["Saved cells: Cells 1 — 2 shown"]
    assert ax.get_title().endswith("· highlighting Cells 1")


def test_the_rest_of_the_cloud_is_faded_not_removed(ctx):
    """Each well's cloud keeps its own artist and colour, at FADE of the opacity it had."""
    from review.features.scatter._cell_rings import FADE, SET_GID

    def cloud(ax):
        return {a.get_gid(): (a.get_alpha() if a.get_alpha() is not None else 1.0, a.get_color())
                for a in ax.lines if a.get_gid() not in (SET_GID, "focused_cell_ring")
                and a.get_marker() not in ("None", "none", "")}

    before = cloud(_redraw(ctx))
    _highlight(ctx)
    after = cloud(_redraw(ctx))
    assert before and set(after) == set(before)
    for gid, (alpha, colour) in after.items():
        assert alpha == pytest.approx(before[gid][0] * FADE) and colour == before[gid][1]


def test_the_focused_cell_keeps_its_large_ring_on_top_after_the_style_pass(ctx):
    from review.core.cell_focus import focus_cell
    from review.core.cell_picking import RING_GID
    from review.features.scatter._cell_rings import SET_GID

    _highlight(ctx)
    focus_cell(ctx, "A01", "1", "24h", 2)
    ax = _redraw(ctx)
    ring = [ln for ln in ax.lines if ln.get_gid() == RING_GID]
    marks = [ln for ln in ax.lines if ln.get_gid() == SET_GID]
    assert len(ring) == 1 and ring[0].get_markersize() == 14
    assert ring[0].get_zorder() > marks[0].get_zorder()


def test_cells_in_a_re_segmented_well_are_counted_lost_and_excluded_is_said(ctx):
    s = _highlight(ctx)
    ctx.state.cell_sets.stale[s.name] = {"A01": 2}
    ctx.state.cell_sets.exclude = s.name
    ax = _redraw(ctx)
    assert _set_points(ax) == []
    assert _note(ax) == ["Saved cells: Cells 1 — 0 shown · 2 lost to re-segmentation · excluded from analysis"]


def test_a_cell_off_this_plot_is_counted_with_its_reason(ctx):
    _highlight(ctx, SET_CELLS + [("B02", "1", "0h", 1)])
    ax = _redraw(ctx)
    assert _note(ax) == ["Saved cells: Cells 1 — 2 shown · 1 not on this plot: 1 in wells not plotted"]


def test_deleting_the_set_clears_the_highlight_and_a_restored_plot_names_a_missing_set(ctx):
    from review.core.cell_sets import delete_set
    from review.persistence.view_state import _restore_highlight

    s = _highlight(ctx)
    delete_set(ctx, s.name)
    assert ctx.state.cell_sets.highlight is None
    _restore_highlight(ctx, "Rounded")
    ax = _redraw(ctx)
    assert ax.get_title().endswith("· highlighted set ‘Rounded’ no longer exists")
    assert _set_points(ax) == []


def test_the_per_cell_export_marks_the_set_and_leaves_lost_cells_blank(ctx):
    from review.data.data_loading import get_rows
    from review.features.export.cell_scope_compute import build_chunk
    from review.features.scatter.cell_scope_compute import cell_scope

    s = _highlight(ctx)
    hours = tuple(sorted({float(h) for h in get_rows(ctx, "A01")["timepoint_hours"]}))
    scope = cell_scope(ctx, entry_x="GFP", entry_y="MCHERRY", timepoints=hours)
    chunk = build_chunk(get_rows(ctx, "A01"), scope, group="A01", well_label="A01", well_name="A01")
    col = "in_saved_cells[Cells 1]"
    marked = chunk.loc[chunk[col] == 1, ["timepoint", "nucleus_id"]].astype(str).values.tolist()
    assert sorted(marked) == [["0h", "5"], ["24h", "2"]]
    ctx.state.cell_sets.stale[s.name] = {"A01": 2}
    chunk = build_chunk(get_rows(ctx, "A01"), cell_scope(ctx, entry_x="GFP", entry_y="MCHERRY",
                                                            timepoints=hours),
                        group="A01", well_label="A01", well_name="A01")
    assert chunk[col].isna().all()



def test_on_a_density_coloured_cloud_the_set_is_outlined_not_filled(ctx):
    from review.core.plot_style import figure_prefs
    from review.features.scatter._cell_rings import SET_GID

    figure_prefs(ctx, ctx.widgets.get("_scatter_fig"))["scatter_density"] = True
    _highlight(ctx)
    ax = _redraw(ctx)
    marks = [ln for ln in ax.lines if ln.get_gid() == SET_GID]
    assert len(marks) == 1 and marks[0].get_markerfacecolor() == "none"


def test_the_scatter_title_naming_a_long_set_stays_inside_the_figure(ctx):
    """Through the real redraw and style pass: a set named at length must not
    push the title's end — the set's name — off the figure."""
    from review.core.cell_sets import add_set, set_highlighted

    s = add_set(ctx, SET_CELLS, source="test", name="Rounded mitotic cells from the 24 h lasso")
    set_highlighted(ctx, s.name)
    ax = _redraw(ctx)
    fig = ax.figure
    fig.canvas.draw()
    box = ax.title.get_window_extent(fig.canvas.get_renderer())
    assert 0 <= box.x0 and box.x1 <= fig.bbox.width, (box, fig.bbox.width, ax.get_title())
    assert ax.get_title().replace("\n", " ").endswith("· highlighting " + s.name)
