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
    assert _rows(page.gates) == [
        ("Bright", "GFP Mean Intensity × MCHERRY Mean Intensity", "A01", "Not applied — a view only"),
        ("Dim", "GFP Mean Intensity × MCHERRY Mean Intensity", "all", "Not applied — a view only"),
    ]
    cg.use_scatter_gates, cg.gating_choices = True, {"Dim"}
    cg.use_scatter_thresh, cg.thresh_choices = True, None
    page.refresh()
    assert [r[3] for r in _rows(page.gates)] == [
        "Defines the ON cells", "Defines the Included cells · Defines the ON cells"]


def test_intensity_floors_are_listed_only_while_they_are_in_force(ctx):
    cg = ctx.state.cell_gating
    cg.fluor_gates = {"gfp": 120.0, "mcherry": 0.0}
    page = _page(ctx)
    assert page.intensity.text() == "Intensity gates in force: GFP ≥ 120"
    cg.use_scatter_gates = True                      # scatter gates replace the floors
    page.refresh()
    assert page.intensity.text() == "No intensity gate is in force."


@pytest.mark.parametrize("width", [1260, 360])
def test_each_list_is_as_tall_as_its_rows_at_a_desk_and_a_phone_width(ctx, width):
    """A row is as tall as its wrapped text at the width the table ends up with —
    measured at an earlier, narrower width, a desk row was four lines tall."""
    from PySide6.QtWidgets import QApplication, QWidget

    from review.core.cell_sets import add_set
    from review.features.saved_cells.tab_view import build_saved_cells_tab

    add_set(ctx, _CELLS, source="lasso · Spatial map")
    ctx.state.cell_gating.scatter_gates = [dict(_GATE), dict(_GATE, name="Dim", wells=[])]
    host = QWidget()
    host.resize(width, 900)
    build_saved_cells_tab(ctx, host)
    host.show()
    for _ in range(5):
        QApplication.processEvents()
    page = ctx.widgets.get("_saved_cells_page")
    for table in (page.sets, page.gates):
        floor = table.verticalHeader().minimumSectionSize()
        rows = [table.rowHeight(r) for r in range(table.rowCount())]
        assert rows == [max(floor, table.sizeHintForRow(r)) for r in range(table.rowCount())]
        assert table.height() == (table.horizontalHeader().height() + sum(rows)
                                  + 2 * table.frameWidth())
    host.close()


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
