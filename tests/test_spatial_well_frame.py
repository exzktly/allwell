"""Spatial on a nanowell run (tracker I2): cells placed in the well frame in µm, timed from their labels.

A nanowell run's cell table states each cell's position in the well frame (``well_x_um``, ``well_y_um``), shared by
every tile of the well, and carries the timepoint as a label with no ``timepoint_hours`` column. Spatial used to
need a pixel centroid and that column, so it showed nothing for these datasets. Driven through the real builder
on a real run's output."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")

from review.features.spatial import collect  # noqa: E402
from tests.test_nanowell_review_load import run_with_a_cell  # noqa: E402,F401  (module fixture)


def test_the_well_frame_wins_over_the_field_centroid_and_names_its_unit():
    both = pd.DataFrame(columns=["well_x_um", "well_y_um", "shape_centroid_row", "shape_centroid_col"])
    assert collect.position_columns(both) == ("well_x_um", "well_y_um", "µm")
    assert collect.position_columns(pd.DataFrame(columns=["shape_centroid_row", "shape_centroid_col"])) == (
        "shape_centroid_col", "shape_centroid_row", "px")
    assert collect.position_columns(pd.DataFrame(columns=["well_x_um"])) is None


def test_the_spatial_tab_draws_a_nanowell_run_s_cells_at_their_well_frame_positions(qtbot, run_with_a_cell):  # noqa: F811
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.features.spatial import controller as spc
    from review.features.spatial.tab_view import build_spatial_tab
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, run_with_a_cell)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    build_spatial_tab(ctx, frame)
    ctx.state.selection.selected_wells = {"A01"}
    assert collect.available_timepoints(ctx) == [0.0]                         # "0h", read from the label
    spc.sp_recompute(ctx)
    cells = pd.read_csv(run_with_a_cell / "measurements_A01.csv.gz")
    st = ctx.state.feature_slice("spatial")
    points = st.frames[0]["points"]
    assert np.allclose(points, cells[["well_x_um", "well_y_um"]].to_numpy())
    fig = ctx.widgets.get("_sp_fig") if ctx.widgets.get_or_none("_sp_fig") else ctx.widgets.get("_sp_canvas").figure
    labels = {(ax.get_xlabel(), ax.get_ylabel()) for ax in fig.axes}
    assert ("x (µm)", "y (µm)") in labels


def test_the_spatial_cell_export_holds_the_nanowell_run_s_cells_it_drew(qtbot, run_with_a_cell):  # noqa: F811
    """The tab's "Save cells" reads the same position columns and the same hours as its figure: a nanowell cell
    table has no field centroid and no ``timepoint_hours``, and requiring either exported nothing."""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.features.export.cell_scope_compute import population_mask
    from review.features.spatial import controller as spc
    from review.features.spatial.cell_scope_compute import cell_scope
    from review.features.spatial.tab_view import build_spatial_tab
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, run_with_a_cell)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    build_spatial_tab(ctx, frame)
    ctx.state.selection.selected_wells = {"A01"}
    spc.sp_recompute(ctx)
    scope = cell_scope(ctx)
    cells = pd.read_csv(run_with_a_cell / "measurements_A01.csv.gz")
    assert scope.required_columns == ("well_x_um", "well_y_um") and scope.timepoints == (0.0,)
    assert int(population_mask(cells, scope).sum()) == len(st_points := ctx.state.feature_slice("spatial").frames[0]
                                                           ["points"]) == len(cells)
    assert ("geometry", "positions are well_x_um / well_y_um (µm); every spatial statistic on this tab is computed "
                        "from those") in scope.notes
