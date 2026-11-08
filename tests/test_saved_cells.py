"""Saved cells: lasso, keep on disk, mark stale, show in the Cell Browser, exclude (tracker M4–M6).

Driven in a production context on the shape-feature mini dataset, through the
real lasso helper, the real Cell Browser collector and the real Cell Gating
panel. The fixtures put the set's cells *against* the plate's row order and
across fields, so an implementation that took the first N rows, or matched on
nucleus id alone, picks other cells.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def ctx(qtbot, mini_dataset_shape):
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset_shape)
    ctx.state.dataset.data_dir = mini_dataset_shape
    return ctx


def _rows(ctx, well="A01"):
    from review.data.data_loading import get_rows
    return get_rows(ctx, well)


def _cells(ctx, picks):
    """``(well, fov, timepoint, nucleus_id)`` of rows *picks* of A01, as the rows spell them."""
    rows = _rows(ctx)
    return [("A01", str(rows.iloc[i]["fov"]), str(rows.iloc[i]["timepoint"]), int(rows.iloc[i]["nucleus_id"]))
            for i in picks]


def _plotted(cells, sampled=None):
    from review.core.cell_picking import PlottedCells
    n = len(cells)
    return PlottedCells(xs=np.arange(n, dtype=float), ys=np.zeros(n),
                        wells=np.array([c[0] for c in cells], dtype=object),
                        fovs=np.array([c[1] for c in cells], dtype=object),
                        tps=np.array([c[2] for c in cells], dtype=object),
                        nids=np.array([c[3] for c in cells]), sampled=sampled)


def test_a_lasso_saves_exactly_the_cells_inside_it_and_says_when_it_was_sampled(ctx):
    from review.core.cell_lasso_view import save_lassoed

    drawn = _cells(ctx, [5, 0, 3, 1])                   # x = 0, 1, 2, 3
    lasso = [(0.5, -1), (2.5, -1), (2.5, 1), (0.5, 1)]  # round x = 1 and 2 only
    saved = save_lassoed(ctx, _plotted(drawn, sampled=(4, 400)), lasso, source="lasso · test")
    from domain.image_identity import CellKey
    want = {CellKey.from_row({"fov": f, "timepoint": t, "nucleus_id": n}, well=w).as_tuple()
            for w, f, t, n in drawn[1:3]}
    assert saved.cells == frozenset(want)
    assert saved.note == "drawn on 4 sampled cells of 400"
    assert saved.name == "Cells 1"


def test_the_cell_browser_shows_the_saved_cells_and_only_them(ctx):
    from review.core.cell_sets import add_set
    from review.features.image_browser.cell_browser.collect import collect_cells

    picks = [7, 2, 11]
    add_set(ctx, _cells(ctx, picks), source="test")
    from review.features.image_browser.cell_browser.state import CellBrowserState
    ctx.runtime.cell_browser.state = CellBrowserState(saved_cells="Cells 1")
    got = collect_cells(ctx, [], [], [], None)
    rows = _rows(ctx)
    want = sorted((str(rows.iloc[i]["fov"]), str(rows.iloc[i]["timepoint"]), str(rows.iloc[i]["nucleus_id"]))
                  for i in picks)
    assert sorted((c.fov, c.tp_token, str(c.nucleus_id)) for c in got) == want


def test_saving_changes_nothing_and_excluding_leaves_the_cells_out(ctx):
    from review.core.cell_sets import add_set, set_excluded
    from review.data.data_loading import df_included_mask
    from review.data.saved_cells_gate import SAVED_CELLS_COLUMN

    picks = [4, 9]
    add_set(ctx, _cells(ctx, picks), source="test")
    assert SAVED_CELLS_COLUMN not in _rows(ctx).columns            # saved, not excluded
    set_excluded(ctx, "Cells 1")
    mask = df_included_mask(_rows(ctx)).to_numpy()
    assert [i for i in range(len(mask)) if not mask[i]] == picks
    set_excluded(ctx, None)
    assert SAVED_CELLS_COLUMN not in _rows(ctx).columns and df_included_mask(_rows(ctx)).all()


def test_a_well_segmented_again_makes_its_cells_stale_not_gone():
    from review.core.cell_sets_state import CellSetsState
    from review.persistence.cell_sets import from_payload, to_payload
    from review.core.cell_sets_state import CellSet

    state = CellSetsState(sets=[CellSet(name="S", cells=frozenset({("A01", "1", "0", "3"), ("B02", "1", "0", "4")}),
                                         fingerprints={"A01": "111", "B02": "222"})], exclude="S")
    payload = to_payload(state)
    sets, stale, exclude = from_payload(payload, {"A01": "111", "B02": "999"})    # B02 re-run
    assert [s.cells for s in sets] == [state.sets[0].cells] and exclude == "S"
    assert stale == {"S": {"B02": 1}}


def test_a_stale_set_names_what_it_leaves_out(ctx):
    from review.core.cell_sets import add_set, stale_summary, usable_cells

    s = add_set(ctx, _cells(ctx, [0, 1]), source="test")
    ctx.state.cell_sets.stale[s.name] = {"A01": 2}
    assert usable_cells(ctx, s) == frozenset()
    assert stale_summary(ctx, s) == ("stale: 2 of 2 cells are in wells segmented again since the set "
                                     "was saved (A01) and are left out")


def test_the_cell_gating_panel_lists_renames_and_excludes(ctx):
    from review.core.cell_sets import add_set
    from review.features.home.cell_gating.saved_cells_view import SavedCellsPanel

    panel = SavedCellsPanel(ctx, ctx.window)
    ctx.widgets.register("_saved_cells_panel", panel)
    assert panel.empty.isVisibleTo(panel) and panel.list.count() == 0
    add_set(ctx, _cells(ctx, [3]), source="lasso · Spatial map")
    assert panel.list.count() == 1 and panel.list.item(0).text() == "Cells 1 — 1 cells · lasso · Spatial map"
    panel.list.setCurrentRow(0)
    panel.name.setText("Rounded")
    panel._rename()
    assert [s.name for s in ctx.state.cell_sets.sets] == ["Rounded"]
    panel.exclude.setCurrentIndex(panel.exclude.findData("Rounded"))
    assert ctx.state.cell_sets.exclude == "Rounded"


def test_the_reset_clears_the_sets_and_the_exclusion(ctx):
    from review.core.cell_sets import add_set
    from review.persistence import reset_scopes

    add_set(ctx, _cells(ctx, [3]), source="t")
    ctx.state.cell_sets.exclude = "Cells 1"
    scope = next(s for s in reset_scopes.ANALYSIS_SCOPES if s.key == "saved_cells")
    scope.clear(ctx)
    assert ctx.state.cell_sets.sets == [] and ctx.state.cell_sets.exclude is None
