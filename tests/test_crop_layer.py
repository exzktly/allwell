"""Crop layer (dataset-wide centroid crop) — pure verdict + chokepoint compose.

Qt-free; needs pandas. Exercises the inside-the-box math, fail-open behaviour,
the manual-AND-QC-AND-crop composition at the inclusion chokepoint, and the
controller's cache projection against a lightweight fake app.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.data.data_loading import df_included_mask
from well_viewer.features.crop.region import (
    compute_crop_pass, normalize_region,
)
from well_viewer.features.crop import controller as crop


def _df(rows, cols):
    return pd.DataFrame({
        "shape_centroid_row": rows,
        "shape_centroid_col": cols,
    })


# ── normalize_region ──────────────────────────────────────────────────────────
def test_normalize_sorts_and_rejects_zero_area():
    assert normalize_region(10, 20, 2, 4) == (2, 4, 10, 20)
    assert normalize_region(5, 5, 5, 9) is None   # zero height
    assert normalize_region(5, 5, 9, 5) is None   # zero width


# ── verdict ───────────────────────────────────────────────────────────────────
def test_none_region_passes_everything():
    df = _df([0, 50, 999], [0, 50, 999])
    assert list(compute_crop_pass(df, None)) == [1, 1, 1]


def test_inside_outside_vector():
    # fixture-style centroids: cell i → row=2i, col=3i for i in 1..6
    rows = [2, 4, 6, 8, 10, 12]
    cols = [3, 6, 9, 12, 15, 18]
    df = _df(rows, cols)
    # keep cells with row<=8 AND col<=12 → cells 1..4 (rows 2,4,6,8; cols 3,6,9,12)
    res = compute_crop_pass(df, (0, 0, 8, 12))
    assert list(res) == [1, 1, 1, 1, 0, 0]


def test_inclusive_boundary_kept():
    df = _df([8], [12])  # exactly on the (8,12) corner
    assert list(compute_crop_pass(df, (0, 0, 8, 12))) == [1]


def test_missing_centroid_columns_fail_open():
    df = pd.DataFrame({"area_px": [10, 20, 30]})
    assert list(compute_crop_pass(df, (0, 0, 5, 5))) == [1, 1, 1]


def test_nan_centroid_passes():
    df = _df([2.0, float("nan")], [3.0, float("nan")])
    # second cell unplaceable → kept even though it's "outside" a tiny box
    assert list(compute_crop_pass(df, (0, 0, 1, 1))) == [0, 1]


# ── chokepoint composition: manual-keep AND passes-QC AND inside-crop ──────────
def test_df_included_mask_composes_crop():
    df = pd.DataFrame({"Included": [1, 1, 1], "QC_Pass": [1, 1, 1],
                       "Crop_Pass": [1, 0, 1]})
    assert list(df_included_mask(df)) == [True, False, True]


def test_df_included_mask_crop_absent_is_unchanged():
    df = pd.DataFrame({"Included": [1, 0, 1]})
    assert list(df_included_mask(df)) == [True, False, True]


def test_df_included_mask_three_way_and():
    df = pd.DataFrame({"Included": [1, 1, 1, 1], "QC_Pass": [1, 1, 0, 1],
                       "Crop_Pass": [1, 0, 1, 1]})
    assert list(df_included_mask(df)) == [True, False, False, True]


# ── controller projection ─────────────────────────────────────────────────────
def _fake_app(cache, region=None):
    app = SimpleNamespace(
        _app_state=SimpleNamespace(
            dataset=SimpleNamespace(cache=cache, crop_region=region)))
    # The crop projector takes ``ctx`` (R1); a minimal ctx over the same state.
    app._ctx = SimpleNamespace(state=app._app_state, app=app)
    return app


def test_apply_crop_to_cache_writes_crop_pass_column():
    cache = {"A01": _df([2, 50], [3, 60])}
    app = _fake_app(cache, region=(0, 0, 10, 10))
    crop.apply_crop_to_cache(app._ctx, "A01")
    assert "Crop_Pass" in cache["A01"].columns
    assert list(cache["A01"]["Crop_Pass"]) == [1, 0]


def test_apply_crop_to_cache_no_region_is_all_pass():
    cache = {"A01": _df([2, 50], [3, 60])}
    app = _fake_app(cache, region=None)
    crop.apply_crop_to_cache(app._ctx)
    assert list(cache["A01"]["Crop_Pass"]) == [1, 1]
