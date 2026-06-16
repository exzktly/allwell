"""Pure-logic tests for the Cell Browser collection layer (no Qt)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from well_viewer.features.cell_browser.collect import (
    Criterion,
    collect_cells,
    enumerate_properties,
)


def _df():
    return pd.DataFrame({
        "Included": [1, 1, 1, 1],
        "area_px": [100.0, 200.0, 300.0, 400.0],
        "timepoint_hours": [0.0, 0.0, 24.0, 24.0],
        "timepoint": ["0", "0", "24", "24"],
        "fov": ["1", "2", "1", "2"],
        "filename": [f"img_{i}.tif" for i in range(4)],
        "nucleus_id": ["10", "11", "12", "13"],
        "gfp_mean_intensity": [100.0, 600.0, 700.0, 50.0],
        "cy5_smfish_count": [0, 5, 9, 1],
        "shape_circularity": [0.2, 0.9, 0.95, float("nan")],
        "shape_centroid_row": [10.0, 20.0, 30.0, 40.0],
        "shape_centroid_col": [11.0, 21.0, 31.0, 41.0],
    })


def _app(df):
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": None}),
        selection=SimpleNamespace(selected_wells=["A01"]),
        channel=SimpleNamespace(ratio_index={}),
    )
    app._get_rows = lambda label: df
    app._parse_rc = lambda s: (s, 0, 0)
    return app


# ── enumerate_properties ─────────────────────────────────────────────────────

def test_enumerate_lists_all_property_families():
    app = _app(_df())
    cols = dict(enumerate_properties(app))
    assert "gfp_mean_intensity" in cols
    assert "cy5_smfish_count" in cols
    assert "shape_circularity" in cols
    # Labels are human-friendly, not raw column names.
    assert cols["shape_circularity"] == "Circularity"


# ── collect_cells ────────────────────────────────────────────────────────────

def test_single_criterion_min():
    app = _app(_df())
    cells = collect_cells(app, [Criterion("gfp_mean_intensity", lo=500.0)])
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]  # 600, 700


def test_min_and_max_band():
    app = _app(_df())
    cells = collect_cells(app, [Criterion("shape_circularity", lo=0.5, hi=0.92)])
    assert [c.nucleus_id for c in cells] == ["11"]  # 0.9 only; 0.95 too high, NaN fails


def test_multiple_criteria_anded():
    app = _app(_df())
    cells = collect_cells(app, [
        Criterion("gfp_mean_intensity", lo=500.0),
        Criterion("cy5_smfish_count", lo=8.0),
    ])
    assert [c.nucleus_id for c in cells] == ["12"]  # gfp700 & spots9


def test_timepoint_filter():
    app = _app(_df())
    cells = collect_cells(app, [], timepoints=[24.0])
    assert sorted(c.nucleus_id for c in cells) == ["12", "13"]


def test_fov_filter():
    app = _app(_df())
    cells = collect_cells(app, [], fovs=["1"])
    assert sorted(c.nucleus_id for c in cells) == ["10", "12"]


def test_descriptor_carries_image_locators():
    app = _app(_df())
    cells = collect_cells(app, [Criterion("gfp_mean_intensity", lo=650.0)])
    c = cells[0]
    assert c.well == "A01" and c.fov == "1" and c.tp_token == "24"
    assert c.timepoint_h == 24.0 and c.filename == "img_2.tif"
    assert c.centroid_row == 30.0 and c.centroid_col == 31.0


def test_nan_fails_active_bound():
    app = _app(_df())
    # row 3 (nid 13) has NaN circularity → excluded by any circularity bound.
    cells = collect_cells(app, [Criterion("shape_circularity", lo=0.0)])
    assert "13" not in [c.nucleus_id for c in cells]


def test_empty_criteria_returns_all():
    app = _app(_df())
    cells = collect_cells(app, [])
    assert len(cells) == 4


def test_no_selection_returns_empty():
    app = _app(_df())
    app._app_state.selection.selected_wells = []
    assert collect_cells(app, [Criterion("gfp_mean_intensity", lo=0.0)]) == []
