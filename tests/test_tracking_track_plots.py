"""Track Plots core (§D / PR5d): per-track series + per-well average."""

from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.tracking.track_plots import (
    compute_track_series, plottable_metrics, TrackSeries,
)


def _well_df():
    # two tracks over 3 timepoints; metric "gfp_mean_intensity"
    rows = []
    for tp, v1, v2 in (("0", 100, 200), ("24h", 110, 220), ("48h", 120, 240)):
        rows.append(dict(well="A01", fov="F001", timepoint=tp, nucleus_id=1,
                         shape_centroid_row=1, shape_centroid_col=1,
                         gfp_mean_intensity=v1, track_id=1, track_parent_id=-1, tree_id=1))
        rows.append(dict(well="A01", fov="F001", timepoint=tp, nucleus_id=2,
                         shape_centroid_row=2, shape_centroid_col=2,
                         gfp_mean_intensity=v2, track_id=2, track_parent_id=-1, tree_id=2))
    return pd.DataFrame(rows)


def test_plottable_metrics_excludes_structural():
    cols = plottable_metrics(_well_df())
    assert "gfp_mean_intensity" in cols
    for excluded in ("track_id", "nucleus_id", "tree_id", "shape_centroid_row", "well"):
        assert excluded not in cols


def test_compute_track_series_per_track_and_average():
    ts = compute_track_series(_well_df(), "gfp_mean_intensity")
    assert isinstance(ts, TrackSeries) and ts.n_tracks == 2
    # each track is time-ordered with its own values
    by_id = {tid: (t, v) for tid, t, v in ts.tracks}
    assert list(by_id[1][1]) == [100, 110, 120]
    assert list(by_id[2][1]) == [200, 220, 240]
    assert list(by_id[1][0]) == [0.0, 24.0, 48.0]   # parsed hours, sorted
    # average track = per-timepoint mean across the two tracks
    assert list(ts.avg_times) == [0.0, 24.0, 48.0]
    assert list(ts.avg_values) == [150.0, 165.0, 180.0]


def test_compute_track_series_empty_cases():
    assert compute_track_series(pd.DataFrame(), "x").n_tracks == 0
    df = _well_df()
    df["track_id"] = -1                        # nothing tracked
    assert compute_track_series(df, "gfp_mean_intensity").n_tracks == 0
    # missing metric column → empty
    assert compute_track_series(_well_df(), "no_such_metric").n_tracks == 0


def test_compute_track_series_per_fov_separation():
    a = _well_df()
    b = _well_df()
    b["fov"] = "F002"
    ts = compute_track_series(pd.concat([a, b], ignore_index=True), "gfp_mean_intensity")
    assert ts.n_tracks == 4                     # 2 FOVs × 2 tracks, kept distinct
