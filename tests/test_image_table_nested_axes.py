"""Image Table nested / compound axes: mixed-radix product + per-cell decode.

Exercises the pure layout math that powers >2 varying dimensions (e.g. a
time-series of every channel for every well): ``_product_values`` (the
outer→inner Cartesian product of a dim list) and ``_layout_cell_values_nested``
(decoding a product ``(row_tuple, col_tuple)`` back to per-cell well/chan/tp/fov).
Both live in the Qt-free ``grid_layout`` layer and are re-exported through the
controller; the flat single-dim case must stay byte-for-byte identical to the
legacy ``_layout_cell_values`` so saved layouts don't regress.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402
from well_viewer.features.image_browser.image_table.state import ImageTableState  # noqa: E402


def _fake_app(*, wells, channels, active_wells=None, active_channels=None,
              timepoints=None, fovs=None, active_timepoints=None, active_fovs=None):
    info = {
        "available_timepoints": timepoints or [],
        "available_fovs": fovs or [],
        "nuclear_token": "",
    }
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(
            active_wells=set(active_wells or []),
            active_channels=set(active_channels or []),
            active_timepoints=set(active_timepoints or []),
            active_fovs=set(active_fovs or []),
        ),
    )
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    return app


def test_product_values_outer_inner_order():
    app = _fake_app(wells=["A01", "A02"], channels=["gfp", "rfp"])
    chans = C._channel_options(app._ctx)  # ['GFP', 'RFP']
    prod = C._product_values(app._ctx, ["well", "channel"])
    # outer = well (slowest), inner = channel (fastest)
    assert prod == [
        ("A01", chans[0]), ("A01", chans[1]),
        ("A02", chans[0]), ("A02", chans[1]),
    ]


def test_product_values_empty_dims_is_single_implicit_cell():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    assert C._product_values(app._ctx, []) == [()]


def test_product_values_single_dim_matches_axis_values():
    app = _fake_app(wells=["A01", "A02", "B01"], channels=["gfp"])
    prod = C._product_values(app._ctx, ["well"])
    assert prod == [(w,) for w in C._axis_values(app._ctx, "well")]


def test_product_values_empty_when_a_dim_has_no_values():
    # no fovs available → product collapses to empty
    app = _fake_app(wells=["A01"], channels=["gfp"], timepoints=[6, 24])
    assert C._product_values(app._ctx, ["timepoint", "fov"]) == []


def test_layout_cell_values_nested_decodes_tuples_and_holds_fixed():
    fixed = {"well": "A01", "channel": "GFP", "timepoint": "0", "fov": "1"}
    row_dims = ["well", "channel"]
    col_dims = ["timepoint"]
    v = C._layout_cell_values_nested(
        row_dims, col_dims, ("A02", "RFP"), ("24",), fixed)
    assert v == {"well": "A02", "channel": "RFP", "timepoint": "24", "fov": "1"}


def test_layout_cell_values_nested_matches_flat_for_single_dims():
    # a nested layout with one dim per axis must equal the legacy flat decode
    fixed = {"well": "", "channel": "", "timepoint": "0", "fov": "1"}
    rows = ["A01", "A02"]
    cols = ["GFP", "RFP"]
    for r in range(len(rows)):
        for c in range(len(cols)):
            flat = C._layout_cell_values("well", "channel", rows, cols, fixed, r, c)
            nested = C._layout_cell_values_nested(
                ["well"], ["channel"], (rows[r],), (cols[c],), fixed)
            assert nested == flat


def test_active_timepoint_and_fov_filters_narrow_the_product():
    app = _fake_app(wells=["A01"], channels=["gfp"],
                    timepoints=[0, 6, 24], fovs=[1, 2, 3],
                    active_timepoints={"6", "24"}, active_fovs={"2"})
    # timepoint dim honours active_timepoints; fov dim honours active_fovs
    assert C._axis_values(app._ctx, "timepoint") == ["6", "24"]
    assert C._axis_values(app._ctx, "fov") == ["2"]
    prod = C._product_values(app._ctx, ["timepoint", "fov"])
    assert prod == [("6", "2"), ("24", "2")]


def test_effective_dims_fall_back_to_single_axis():
    st = ImageTableState(row_axis="well", col_axis="timepoint")
    # empty lists → legacy single axis (no migration needed for old view-state)
    assert st.effective_row_dims() == ["well"]
    assert st.effective_col_dims() == ["timepoint"]
    st.row_dims = ["well", "channel"]
    st.col_dims = ["timepoint"]
    assert st.effective_row_dims() == ["well", "channel"]
    assert st.effective_col_dims() == ["timepoint"]
