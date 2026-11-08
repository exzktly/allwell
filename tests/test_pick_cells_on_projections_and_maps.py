"""Cells on Multivariate's projections and Spatial's maps can be clicked and found (tracker M1–M3).

Both tabs drew one point per cell carrying nothing but its well, so a point
could not be opened and the focused cell could not be found on them. These
build each tab in a production context on the shape-feature mini dataset
(fields, timepoints and nucleus ids per row), recompute it, press the mouse on
a drawn point through matplotlib's own event path, and check which cell became
focused and where the ring went.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def shell(qtbot, mini_dataset_shape):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset_shape)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    ctx.state.selection.selected_wells = {"A01"}
    return ctx, frame


def _press_on(canvas, ax, x, y):
    from matplotlib.backend_bases import MouseEvent

    canvas.draw()
    px, py = ax.transData.transform((x, y))
    canvas.callbacks.process("button_press_event",
                             MouseEvent("button_press_event", canvas, px, py, button=1))


def _rings(ax):
    from review.core.cell_picking import RING_GID
    return [(float(ln.get_xdata()[0]), float(ln.get_ydata()[0]))
            for ln in ax.lines if ln.get_gid() == RING_GID]


def _row(ctx, well, fov, tp, nid):
    from review.data.data_loading import get_rows
    from review.core.cell_focus import same_frame
    from review.core.focus_state import FocusedCell

    rows = get_rows(ctx, well)
    cell = FocusedCell(well, str(fov), str(tp), int(nid))
    hit = [i for i, r in rows.iterrows()
           if int(r["nucleus_id"]) == cell.nucleus_id and same_frame(cell, well, str(r["fov"]), str(r["timepoint"]))]
    assert len(hit) == 1, hit
    return rows.loc[hit[0]]


def test_a_click_on_a_spatial_map_focuses_that_cell_and_rings_it(shell):
    from review.core.cell_focus import focused_cell
    from review.core.cell_picking import cells_of
    from review.features.spatial import controller as spc
    from review.features.spatial.tab_view import build_spatial_tab

    ctx, frame = shell
    build_spatial_tab(ctx, frame)
    spc.sp_recompute(ctx)
    fig = ctx.widgets.get("_sp_canvas").figure
    ax = next(a for a in fig.axes if cells_of(a) is not None)
    cells = cells_of(ax)
    i = len(cells) - 1
    _press_on(ax.figure.canvas, ax, cells.xs[i], cells.ys[i])

    cell = focused_cell(ctx)
    assert cell is not None and cell.nucleus_id == int(cells.nids[i])
    row = _row(ctx, cell.well, cell.fov, cell.tp, cell.nucleus_id)
    # The focused cell is the row drawn at that point: its centroid is the point.
    assert (row["shape_centroid_col"], row["shape_centroid_row"]) == pytest.approx((cells.xs[i], cells.ys[i]))
    assert _rings(ax) == [pytest.approx((cells.xs[i], cells.ys[i]))]


def test_a_click_on_a_projection_focuses_the_cell_whose_features_it_shows(shell):
    from review.core.cell_focus import focused_cell
    from review.core.cell_picking import cells_of
    from review.features.multivariate import controller as mvc
    from review.features.multivariate import state as mv_state
    from review.features.multivariate.tab_view import build_multivariate_tab

    ctx, frame = shell
    build_multivariate_tab(ctx, frame)
    st = ctx.state.feature_slice("multivariate")
    st.mode = mv_state.MODE_DIMRED
    st.dimred_method = "pca"
    st.feature_cols = ["gfp_mean_intensity", "shape_area"]
    mvc.mv_recompute(ctx)
    fig = ctx.runtime.multivariate.fig
    ax = next(a for a in fig.axes if cells_of(a) is not None)
    cells = cells_of(ax)
    data = st.last_result["data"]
    i = int(np.argmax(cells.xs))
    _press_on(ax.figure.canvas, ax, cells.xs[i], cells.ys[i])

    cell = focused_cell(ctx)
    row = _row(ctx, cell.well, cell.fov, cell.tp, cell.nucleus_id)
    # The matrix row drawn there holds that cell's own feature values.
    assert [row["gfp_mean_intensity"], row["shape_area"]] == pytest.approx(list(data["X"][i]))
    assert _rings(ax) == [pytest.approx((cells.xs[i], cells.ys[i]))]


def test_a_sampled_projection_says_when_the_focused_cell_was_not_drawn():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from types import SimpleNamespace

    from review.core.cell_picking import NOTE_GID, PlottedCells, attach_cells, ring_focused
    from review.core.focus_state import FocusState, FocusedCell

    fig = Figure()
    ax = fig.add_subplot(111)
    attach_cells(ax, PlottedCells(
        xs=np.array([0.0, 1.0]), ys=np.array([0.0, 1.0]), wells=np.array(["A01", "A01"], dtype=object),
        fovs=np.array(["1", "1"], dtype=object), tps=np.array(["0h", "0h"], dtype=object),
        nids=np.array([1, 2]), sampled=(2, 50), scope_wells=frozenset({"A01"})))
    ctx = SimpleNamespace(state=SimpleNamespace(focus=FocusState(FocusedCell("A01", "1", "0h", 9))))
    assert ring_focused(ctx, fig) == 0
    notes = [t.get_text() for t in ax.texts if t.get_gid() == NOTE_GID]
    assert notes == ["The focused cell (A01 · 1 · 0h · #9) is not among the 2 sampled cells of 50."]
    ctx.state.focus.cell = FocusedCell("B05", "1", "0h", 9)      # a well this plot does not cover
    ring_focused(ctx, fig)
    assert [t for t in ax.texts if t.get_gid() == NOTE_GID] == []


def test_the_umap_sample_keeps_each_cell_s_identity_with_its_features():
    """The slow projections draw a seeded sample; the identity arrays must be
    sliced with the matrix, or a click names a different cell than the point."""
    from review.features.multivariate.controller import _subsample_for_projection

    n = 6000
    X = np.arange(n, dtype=float).reshape(-1, 1)
    data = {"X": X, "group": np.full(n, "g", dtype=object), "well": np.full(n, "A01", dtype=object),
            "timepoint": np.zeros(n), "fov": np.array([str(i) for i in range(n)], dtype=object),
            "tp_token": np.full(n, "0h", dtype=object), "nucleus_id": np.arange(n, dtype=np.int64)}
    Xp, sub, total = _subsample_for_projection(X, data, "umap")
    assert total == n and len(Xp) < n
    assert list(sub["nucleus_id"]) == [int(v) for v in Xp[:, 0]]
    assert list(sub["fov"]) == [str(int(v)) for v in Xp[:, 0]]
