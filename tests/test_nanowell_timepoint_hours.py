"""A nanowell run's per-cell CSV carries ``timepoint_hours``, as the per-field path's does.

Without it the per-cell Scatter drew no point on a nanowell plate while the line and bar plots drew normally:
``review/features/scatter/_cells.well_cloud`` selects cells by ``timepoint_hours`` and gives up on a well that has
none, where the aggregates parse the ``timepoint`` label (``review/data/data_loading.resolved_timepoints``).
"""

from __future__ import annotations

import csv
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pipeline import nanowell_task as nt


def _outcome(timepoints):
    n = len(timepoints)
    return SimpleNamespace(cells={
        "label": list(range(1, n + 1)), "timepoint": list(timepoints), "fov": ["1"] * n,
        "area_um2": [100.0] * n, "mean_GFP": [500.0] * n, "mean_RFP": [700.0] * n,
        "nw_q": [0] * n, "nw_r": [0] * n, "nw_ring": [0] * n, "nw_label": ["0,0"] * n,
        "well_x_um": [0.0] * n, "well_y_um": [0.0] * n,
    })


@pytest.mark.parametrize("token, hours", [("0h", 0.0), ("24h", 24.0), ("1d02h30m", 26.5), ("2d", 48.0), ("", 0.0)])
def test_a_timepoint_token_is_read_as_the_per_field_path_reads_it(token, hours):
    assert nt.timepoint_hours(token) == hours


def test_an_unreadable_token_is_nan_so_review_falls_back_to_the_label():
    assert math.isnan(nt.timepoint_hours("baseline"))


def test_every_cell_row_carries_its_timepoint_in_hours_beside_the_label():
    rows = nt.cell_records(_outcome(["0h", "24h"]), "A01", ["GFP", "RFP"], {"0h": 1.0, "24h": 1.0})
    assert [(r["timepoint"], r["timepoint_hours"]) for r in rows] == [("0h", 0.0), ("24h", 24.0)]
    assert list(rows[0]) == nt.cell_columns(["GFP", "RFP"])
    columns = nt.cell_columns(["GFP"])
    assert columns[columns.index("timepoint") + 1] == "timepoint_hours"


def test_a_kept_row_from_an_older_csv_gains_the_column_in_place():
    old = {"well": "A01", "fov": "1", "timepoint": "24h", "nucleus_id": "3", "area_px": "100.0"}
    row = nt.with_timepoint_hours(old)
    assert list(row) == ["well", "fov", "timepoint", "timepoint_hours", "nucleus_id", "area_px"]
    assert row["timepoint_hours"] == 24.0
    # A row that already has it is left exactly as read.
    assert nt.with_timepoint_hours({**old, "timepoint_hours": "24.0"})["timepoint_hours"] == "24.0"


def test_an_older_kept_row_ahead_of_a_new_one_writes_one_table(tmp_path):
    # ``_write_single_csv`` takes its header from the first row: a kept row without the column, ahead of this run's
    # rows, would make ``DictWriter`` refuse every new one.
    from pipeline.io import _write_single_csv

    new = nt.cell_records(_outcome(["24h"]), "A01", ["GFP", "RFP"], {"24h": 1.0})
    old = {k: v for k, v in nt.cell_records(_outcome(["0h"]), "A01", ["GFP", "RFP"], {"0h": 1.0})[0].items()
           if k != "timepoint_hours"}
    path = tmp_path / "measurements_A01.csv"
    _write_single_csv([nt.with_timepoint_hours(old)] + new, path)
    with path.open(newline="") as fh:
        assert [(r["timepoint"], float(r["timepoint_hours"])) for r in csv.DictReader(fh)] == [("0h", 0.0),
                                                                                             ("24h", 24.0)]


def test_the_per_cell_scatter_draws_a_nanowell_well():
    from review.features.scatter._cells import well_cloud

    df = pd.DataFrame(nt.cell_records(_outcome(["0h", "24h", "24h"]), "A01", ["GFP", "RFP"],
                                      {"0h": 1.0, "24h": 1.0}))
    cloud = well_cloud(df, col_x="GFP_mean_intensity", col_y="RFP_mean_intensity", target_tps=[24.0],
                       fluor_gate_x=0.0, fluor_gate_y=0.0)
    assert cloud is not None
    _df, x, y, _tp, mask, _ungated, _gated = cloud
    assert np.flatnonzero(mask).tolist() == [1, 2]
