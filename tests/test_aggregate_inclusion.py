"""The bar/line aggregation must gate through the inclusion chokepoint so it
honors QC_Pass and Crop_Pass, not just the raw Included flag.

Regression: aggregate_with_threshold_df filtered ``Included==1`` directly, so the
crop (and QC) layers never reached the line plot even though the Segmentation
overlay and the other plots did.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from well_viewer.data.data_loading import aggregate_with_threshold_df


def _df(included, qc=None, crop=None):
    d = {
        "timepoint_hours": [0.0, 0.0, 0.0, 0.0],
        "gfp_mean_intensity": [10.0, 20.0, 30.0, 40.0],
        "area_px": [100.0, 100.0, 100.0, 100.0],  # required: aggregation no-ops without it
        "Included": included,
    }
    if qc is not None:
        d["QC_Pass"] = qc
    if crop is not None:
        d["Crop_Pass"] = crop
    return pd.DataFrame(d)


def test_aggregation_excludes_qc_and_crop_failures():
    # cells: 0 kept, 1 fails QC, 2 cropped out, 3 kept → 2 included, mean (10+40)/2.
    df = _df([1, 1, 1, 1], qc=[1, 0, 1, 1], crop=[1, 1, 0, 1])
    pts = aggregate_with_threshold_df(df, threshold=0.0, val_col="gfp_mean_intensity")
    assert len(pts) == 1
    _t, mean, _spread, _frac, n_above, n_total = pts[0][:6]
    assert n_total == 2
    assert mean == pytest.approx(25.0)


def test_crop_alone_reduces_the_population():
    full = aggregate_with_threshold_df(_df([1, 1, 1, 1]),
                                       threshold=0.0, val_col="gfp_mean_intensity")
    cropped = aggregate_with_threshold_df(_df([1, 1, 1, 1], crop=[1, 1, 0, 0]),
                                          threshold=0.0, val_col="gfp_mean_intensity")
    assert full[0][5] == 4          # n_total before crop
    assert cropped[0][5] == 2       # n_total after crop drops 2 cells


def test_no_gating_columns_keeps_everything():
    # No QC_Pass / Crop_Pass columns → all-pass (older data), unchanged behavior.
    pts = aggregate_with_threshold_df(_df([1, 1, 1, 0]),
                                      threshold=0.0, val_col="gfp_mean_intensity")
    assert pts[0][5] == 3           # only the Included==0 cell drops
