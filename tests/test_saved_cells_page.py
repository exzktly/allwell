"""The Saved Cells page: every saved set and every gate, and how to view each.

Fast tests drive the real page widget in a production context on the
shape-feature mini dataset: rows are asserted as the strings drawn, and every
button is pressed. The last tests build the real app, open the page through the
rail, and press *View in Cell Browser* and *Manage on Cell Gating* through the
real services, asserting what the Cell Browser then holds.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

pytestmark = pytest.mark.qt


@pytest.fixture
def ctx(qtbot, mini_dataset_shape):
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset_shape)
    ctx.state.dataset.data_dir = mini_dataset_shape
    return ctx


def _page(ctx):
    from PySide6.QtWidgets import QWidget

    from review.features.saved_cells.tab_view import build_saved_cells_tab

    host = QWidget(ctx.window)
    build_saved_cells_tab(ctx, host)
    return ctx.widgets.get("_saved_cells_page")


def _rows(table):
    return [tuple(table.item(r, c).text() for c in range(table.columnCount()))
            for r in range(table.rowCount())]


#: Two cells of A01 and one of B02, nucleus ids against the plate's order.
_CELLS = [("A01", "1", "24h", 2), ("A01", "1", "0h", 5), ("B02", "1", "0h", 1)]
_GATE = {"x0": 0, "y0": 0, "x1": 200, "y1": 200, "x_col": "gfp_mean_intensity",
         "y_col": "mcherry_mean_intensity", "name": "Bright", "wells": ["A01"]}


def test_an_empty_dataset_says_how_to_make_each_kind(ctx):
    page = _page(ctx)
    assert page.sets_empty.isVisibleTo(page) and not page.sets.isVisibleTo(page)
    assert page.gates_empty.isVisibleTo(page) and not page.gates.isVisibleTo(page)
    assert page.intensity.text() == "No intensity gate is in force."


def test_each_set_is_listed_with_its_size_origin_and_what_it_does(ctx):
    from review.core.cell_sets import add_set, set_excluded

    add_set(ctx, _CELLS, source="lasso · Spatial map")
    add_set(ctx, _CELLS[:1], source="lasso · Multivariate", name="Rounded")
    set_excluded(ctx, "Rounded")
    page = _page(ctx)
    assert _rows(page.sets) == [
        ("Cells 1", "3 cells in 2 wells", "lasso · Spatial map", "Saved only — changes no number"),
        ("Rounded", "1 cells in 1 well", "lasso · Multivariate", "Excluded from analysis"),
    ]


def test_a_set_saved_after_the_page_was_built_appears_without_reopening_it(ctx):
    from review.core.cell_sets import add_set

    page = _page(ctx)
    add_set(ctx, _CELLS, source="lasso")
    assert [r[0] for r in _rows(page.sets)] == ["Cells 1"]
    assert not page.sets_empty.isVisibleTo(page)


def test_the_set_buttons_change_the_store_and_undo_on_a_second_press(ctx):
    from review.core.cell_sets import add_set

    add_set(ctx, _CELLS, source="lasso")
    page = _page(ctx)
    st = ctx.state.cell_sets
    page.highlight.click()
    assert st.highlight == "Cells 1" and page.highlight.isChecked()
    assert _rows(page.sets)[0][3] == "Highlighted on Scatter"
    page.exclude.click()
    assert st.exclude == "Cells 1" and _rows(page.sets)[0][3] == \
        "Excluded from analysis · Highlighted on Scatter"
    page.highlight.click()
    page.exclude.click()
    assert st.highlight is None and st.exclude is None
    assert _rows(page.sets)[0][3] == "Saved only — changes no number"


def test_rename_and_delete_act_on_the_selected_set(ctx):
    from review.core.cell_sets import add_set

    add_set(ctx, _CELLS, source="lasso")
    add_set(ctx, _CELLS[:1], source="lasso", name="Other")
    page = _page(ctx)
    page.sets.selectRow(1)
    page.name.setText("Rounded")
    page.name.editingFinished.emit()
    assert [s.name for s in ctx.state.cell_sets.sets] == ["Cells 1", "Rounded"]
    assert [r[0] for r in _rows(page.sets)] == ["Cells 1", "Rounded"]
    page.delete.click()
    assert [s.name for s in ctx.state.cell_sets.sets] == ["Cells 1"]


def test_a_stale_set_names_what_it_leaves_out(ctx):
    from review.core.cell_sets import add_set

    s = add_set(ctx, _CELLS, source="lasso")
    ctx.state.cell_sets.stale[s.name] = {"A01": 2}
    page = _page(ctx)
    assert _rows(page.sets)[0][1] == "3 cells in 2 wells (1 usable)"
    assert page.set_detail.text().endswith(
        "stale: 2 of 3 cells are in wells segmented again since the set was saved (A01) "
        "and are left out")


def test_view_in_cell_browser_asks_for_exactly_the_selected_population(ctx, monkeypatch):
    from review.core.cell_sets import add_set

    calls = []
    monkeypatch.setattr(ctx.features, "query",
                        lambda key, *a, default=None, **k: calls.append((key, a[1:], k)) or True)
    add_set(ctx, _CELLS, source="lasso")
    add_set(ctx, _CELLS[:1], source="lasso", name="Other")
    ctx.state.cell_gating.scatter_gates = [dict(_GATE), dict(_GATE, name="Dim")]
    page = _page(ctx)
    page.sets.selectRow(1)
    page.view_set.click()
    page.gates.selectRow(1)
    page.view_gate.click()
    page.manage.click()
    assert calls == [("cell_browser.browse", ("set:Other",), {"gate": None}),
                     ("cell_browser.browse", ("gates",), {"gate": "Dim"}),
                     ("cell_gating.show", (), {})]


def test_a_build_without_the_cell_browser_says_so(ctx, monkeypatch):
    from review.core.cell_sets import add_set

    said = []
    monkeypatch.setattr(ctx.features, "query", lambda key, *a, default=None, **k: default)
    monkeypatch.setattr(ctx.notify, "set_status", said.append)
    add_set(ctx, _CELLS, source="lasso")
    _page(ctx).view_set.click()
    assert said == ["This build has no Cell Browser to show the cells in."]


def test_a_gate_says_which_layer_applies_it(ctx):
    cg = ctx.state.cell_gating
    cg.scatter_gates = [dict(_GATE), dict(_GATE, name="Dim", wells=[])]
    page = _page(ctx)
    assert [r[:3] + r[4:] for r in _rows(page.gates)] == [
        ("Bright", "GFP Mean Intensity × MCHERRY Mean Intensity", "A01", "Not applied — a view only"),
        ("Dim", "GFP Mean Intensity × MCHERRY Mean Intensity", "all", "Not applied — a view only"),
    ]
    cg.use_scatter_gates, cg.gating_choices = True, {"Dim"}
    cg.use_scatter_thresh, cg.thresh_choices = True, None
    page.refresh()
    assert [r[4] for r in _rows(page.gates)] == [
        "Defines the ON cells", "Defines the Included cells · Defines the ON cells"]


def _inside(ctx, wells, x, y, *, drop=frozenset()):
    """``(inside, of)`` counted straight off the rows, without the gate code: every
    cell of *wells* not in *drop*, and those inside the box ``x`` × ``y``."""
    import pandas as pd

    from review.data.data_loading import get_rows
    inside = of = 0
    for well in wells:
        df = get_rows(ctx, well)
        keep = ~pd.Series([(well, str(f), str(t), int(n)) in drop for f, t, n in
                           zip(df["fov"], df["timepoint"], df["nucleus_id"])], index=df.index)
        gx, gy = df["gfp_mean_intensity"], df["mcherry_mean_intensity"]
        box = gx.between(*x) & gy.between(*y)
        inside += int((box & keep).sum())
        of += int(keep.sum())
    return inside, of


def _count(inside, of):
    return f"{inside:,} of {of:,} ({100.0 * inside / of:.1f}%)"


def test_each_gate_counts_its_cells_in_its_own_wells(ctx):
    """A01 only for the gate drawn there, every loaded well for one that recorded none;
    the box splits each well, so neither "all cells" nor "none" passes."""
    from review.data.data_loading import df_included_mask, get_rows

    wells = sorted(ctx.state.dataset.well_paths)
    assert len(wells) > 1 and all(df_included_mask(get_rows(ctx, w)).all() for w in wells)
    box = {"x0": 100, "x1": 160, "y0": 50, "y1": 80}
    ctx.state.cell_gating.scatter_gates = [dict(_GATE, **box), dict(_GATE, **box, name="Dim", wells=[])]
    a01 = _inside(ctx, ["A01"], (100, 160), (50, 80))
    every = _inside(ctx, wells, (100, 160), (50, 80))
    assert 0 < a01[0] < a01[1] and a01 != every
    page = _page(ctx)
    assert [r[3] for r in _rows(page.gates)] == [_count(*a01), _count(*every)]


def test_a_gate_counts_only_the_cells_the_other_layers_keep(ctx):
    """An excluded saved set leaves the count: its cells inside the box and out."""
    from review.core.cell_sets import add_set, set_excluded
    from review.data.data_loading import get_rows

    box = {"x0": 100, "x1": 160, "y0": 50, "y1": 80}
    ctx.state.cell_gating.scatter_gates = [dict(_GATE, **box)]
    rows = get_rows(ctx, "A01")
    inside_rows = rows[rows["gfp_mean_intensity"].between(100, 160)
                       & rows["mcherry_mean_intensity"].between(50, 80)]
    outside_rows = rows.drop(inside_rows.index)
    pick = [inside_rows.iloc[0], outside_rows.iloc[0]]
    cells = [("A01", str(r["fov"]), str(r["timepoint"]), int(r["nucleus_id"])) for r in pick]
    add_set(ctx, cells, source="test")
    page = _page(ctx)
    before = _inside(ctx, ["A01"], (100, 160), (50, 80))
    assert _rows(page.gates)[0][3] == _count(*before)
    set_excluded(ctx, "Cells 1")
    page.refresh()
    after = _inside(ctx, ["A01"], (100, 160), (50, 80), drop=frozenset(cells))
    assert after == (before[0] - 1, before[1] - 2)
    assert _rows(page.gates)[0][3] == _count(*after)


def test_intensity_floors_are_listed_only_while_they_are_in_force(ctx):
    cg = ctx.state.cell_gating
    cg.fluor_gates = {"gfp": 120.0, "mcherry": 0.0}
    page = _page(ctx)
    assert page.intensity.text() == "Intensity gates in force: GFP ≥ 120"
    cg.use_scatter_gates = True                      # scatter gates replace the floors
    page.refresh()
    assert page.intensity.text() == "No intensity gate is in force."


@pytest.mark.parametrize("width", [1260, 360])
@pytest.mark.parametrize("restyle", [False, True])
def test_each_list_is_as_tall_as_its_rows_at_a_desk_and_a_phone_width(ctx, qtbot, width, restyle):
    """A row is as tall as its wrapped text at the width and style the table ends
    up with. Measured at an earlier, narrower width, a desk row was four lines
    tall; and a stylesheet reaching the table after it was fitted (a theme
    switch, phone layout's rescale) left every row at the old style's height.

    Under the app's real stylesheet, whose row padding Qt's own fit includes, so
    "fitted" is "fitting again changes nothing". Waited for rather than pumped a
    fixed number of times: fitting a list can bring up the page's scroll bar,
    which narrows it and queues another fit."""
    from PySide6.QtWidgets import QApplication, QWidget

    from review.core.cell_sets import add_set
    from review.features.saved_cells.tab_view import build_saved_cells_tab
    from ui import theme

    app = QApplication.instance()
    before = app.styleSheet()
    try:
        theme.apply_app_stylesheet(app, theme.qss())
        add_set(ctx, _CELLS, source="lasso · Spatial map")
        ctx.state.cell_gating.scatter_gates = [dict(_GATE), dict(_GATE, name="Dim", wells=[])]
        host = QWidget()
        host.resize(width, 900)
        build_saved_cells_tab(ctx, host)
        host.show()
        page = ctx.widgets.get("_saved_cells_page")
        if restyle:
            qtbot.wait(50)
            theme.apply_ui_scale(1.3)
            theme.apply_app_stylesheet(app, theme.qss())

        def fitted(table):
            rows = [table.rowHeight(r) for r in range(table.rowCount())]
            table.resizeRowsToContents()
            again = [table.rowHeight(r) for r in range(table.rowCount())]
            for r, h in enumerate(rows):
                table.setRowHeight(r, h)
            return rows == again and table.height() == (
                table.horizontalHeader().height() + sum(rows) + 2 * table.frameWidth())

        qtbot.waitUntil(lambda: fitted(page.sets) and fitted(page.gates), timeout=3000)
        host.resize(width * 2 // 3, 900)               # the window narrowed while shown
        qtbot.waitUntil(lambda: fitted(page.sets) and fitted(page.gates), timeout=3000)
        host.close()
    finally:
        theme.apply_ui_scale(1.0)
        app.setStyleSheet(before)


# ── the real app: the page on the rail, and the services it calls ──────────

@pytest.mark.slow
def test_the_page_opens_from_the_rail_and_its_buttons_reach_the_real_pages(viewer_app, tmp_path):
    """Built by the registry, opened from the rail, and every button taken to the
    page it names: the Cell Browser holding exactly the set's cells, then exactly
    one gate's, then Home → Cell Gating."""
    from PySide6.QtWidgets import QApplication

    from review.core.cell_sets import add_set
    from tests._page_datasets import image_plate

    app = viewer_app
    app._load_path(image_plate(tmp_path / "plate") / "out")
    ctx = app._ctx
    add_set(ctx, [("A02", "F001", "0h", 2)], source="lasso")
    ctx.state.cell_gating.scatter_gates = [
        {"x0": 0, "y0": 0, "x1": 5000, "y1": 5000, "x_col": "gfp_mean_intensity",
         "y_col": "dapi_mean_intensity", "name": "Everything", "wells": []},
        {"x0": -2, "y0": -2, "x1": -1, "y1": -1, "x_col": "gfp_mean_intensity",
         "y_col": "dapi_mean_intensity", "name": "Nothing", "wells": []}]
    ctx.state.selection.selected_wells = {"A01", "A02"}
    nb = app._notebook
    nb.setCurrentByName("Saved Cells")
    QApplication.processEvents()
    page = ctx.widgets.get("_saved_cells_page")
    assert [page.sets.item(r, 0).text() for r in range(page.sets.rowCount())] == ["Cells 1"]
    assert [page.gates.item(r, 0).text() for r in range(page.gates.rowCount())] == \
        ["Everything", "Nothing"]

    def gallery():
        QApplication.processEvents()
        return sorted((c.well, c.fov, str(c.nucleus_id)) for c in ctx.runtime.cell_browser.state.cells)

    page.view_set.click()
    assert app._current_centre_tab() == "Cell Browser"
    assert ctx.runtime.cell_browser.source_cb.currentData() == "set:Cells 1"
    assert gallery() == [("A02", "F001", "2")]

    nb.setCurrentByName("Saved Cells")
    page.gates.selectRow(0)
    page.view_gate.click()
    assert ctx.runtime.cell_browser.source_cb.currentData() == "gates"
    assert ctx.runtime.cell_browser.gate_choices == {"Everything"}
    assert gallery() == [("A01", "F001", "1"), ("A01", "F001", "2"),
                         ("A02", "F001", "1"), ("A02", "F001", "2")]
    nb.setCurrentByName("Saved Cells")
    page.gates.selectRow(1)
    page.view_gate.click()
    assert ctx.runtime.cell_browser.gate_choices == {"Nothing"} and gallery() == []

    # A set saved after the Cell Browser's Source menu was built: the jump still finds it.
    add_set(ctx, [("A01", "F001", "0h", 1)], source="lasso", name="Late")
    nb.setCurrentByName("Saved Cells")
    page.sets.selectRow(1)
    page.view_set.click()
    assert ctx.runtime.cell_browser.source_cb.currentData() == "set:Late"
    assert gallery() == [("A01", "F001", "1")]

    nb.setCurrentByName("Saved Cells")
    page.manage.click()
    QApplication.processEvents()
    from review.features.home.controller import _subtabs
    assert nb.currentName() == "Home" and _subtabs(ctx).tabText(_subtabs(ctx).currentIndex()) == "Cell Gating"
