"""Crop effect counts — feedback so a crop that excludes nothing (e.g. a dataset
without centroid columns) is never mistaken for a working crop.

Qt-free: ``crop.region`` and ``crop.controller`` both import without PySide6
(QMessageBox is imported lazily inside the warn path).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.crop.region import crop_effect_for_df
from well_viewer.features.crop import controller as crop


def _df(rows, cols, included=None, qc=None):
    d = {"shape_centroid_row": rows, "shape_centroid_col": cols}
    if included is not None:
        d["Included"] = included
    if qc is not None:
        d["QC_Pass"] = qc
    return pd.DataFrame(d)


# ── per-frame summary ─────────────────────────────────────────────────────────
def test_effect_counts_removed_and_included():
    # cells 1..4 inside (row<=8, col<=12), 5..6 outside
    df = _df([2, 4, 6, 8, 10, 12], [3, 6, 9, 12, 15, 18])
    removed, included, has_centroids = crop_effect_for_df(df, (0, 0, 8, 12))
    assert has_centroids is True
    assert included == 6          # no Included/QC columns → all count
    assert removed == 2           # cells 5,6 dropped by the crop


def test_effect_respects_included_and_qc():
    # cell 5 already excluded (Included=0) and cell 6 fails QC → neither counts
    # toward "otherwise included", so the crop removes 0 of the remaining 4.
    df = _df([2, 4, 6, 8, 10, 12], [3, 6, 9, 12, 15, 18],
             included=[1, 1, 1, 1, 0, 1], qc=[1, 1, 1, 1, 1, 0])
    removed, included, has_centroids = crop_effect_for_df(df, (0, 0, 8, 12))
    assert included == 4          # cells 1..4
    assert removed == 0           # all of 1..4 are inside the box


def test_effect_no_centroids_fails_open():
    df = pd.DataFrame({"area_px": [10, 20, 30], "Included": [1, 1, 1]})
    removed, included, has_centroids = crop_effect_for_df(df, (0, 0, 5, 5))
    assert has_centroids is False
    assert included == 3
    assert removed == 0           # can't place cells → excludes nothing


# ── aggregation over the cache ────────────────────────────────────────────────
def _app(cache, region):
    app = SimpleNamespace(
        _app_state=SimpleNamespace(
            dataset=SimpleNamespace(cache=cache, crop_region=region)))
    # _crop_effect_counts reads the region through get_crop_region(ctx) (R1).
    app._ctx = SimpleNamespace(state=app._app_state, app=app)
    return app


def test_counts_aggregate_over_loaded_wells():
    cache = {
        "A01": _df([2, 10], [3, 15]),   # 1 inside, 1 outside
        "A02": _df([4, 12], [6, 18]),   # 1 inside, 1 outside
    }
    app = _app(cache, (0, 0, 8, 12))
    removed, total_incl, has_centroids, n_wells = crop._crop_effect_counts(app)
    assert (removed, total_incl, has_centroids, n_wells) == (2, 4, True, 2)


def test_counts_flag_missing_centroids_dataset_wide():
    cache = {"A01": pd.DataFrame({"area_px": [1, 2], "Included": [1, 1]})}
    app = _app(cache, (0, 0, 5, 5))
    removed, total_incl, has_centroids, n_wells = crop._crop_effect_counts(app)
    assert has_centroids is False and removed == 0 and n_wells == 1
