"""Pure-Python unit tests for ``DatasetState``.

No Qt, no app — these exercise the dataset behaviour directly: the load-reset
and the canonical timepoint / FOV rebuilds derived from the row cache and
``pipeline_info``.
"""

from __future__ import annotations

import pandas as pd

from well_viewer.state.dataset import DatasetState


def _df(timepoints, fovs=None) -> pd.DataFrame:
    rows = []
    for i, tp in enumerate(timepoints):
        rows.append({
            "timepoint_hours": tp,
            "fov": fovs[i] if fovs else "1",
            "gfp_mean_intensity": 1.0,
        })
    return pd.DataFrame(rows)


# ── status ───────────────────────────────────────────────────────────────────

def test_defaults():
    ds = DatasetState()
    assert ds.has_data is False
    assert ds.well_count == 0


def test_has_data_tracks_well_paths():
    ds = DatasetState(well_paths={"A01": "x", "A02": "y"})
    assert ds.has_data is True
    assert ds.well_count == 2


# ── load lifecycle ───────────────────────────────────────────────────────────

def test_begin_load_resets_caches():
    ds = DatasetState(
        well_paths={"A01": "x"}, cache={"A01": _df([0.0])},
        persistence_doc={"k": 1},
    )
    ds.begin_load("/new/dir")
    assert ds.data_dir == "/new/dir"
    assert ds.well_paths == {}
    assert ds.cache == {}
    assert ds.persistence_doc is None


def test_begin_load_clears_collections_in_place():
    paths = {"A01": "x"}
    ds = DatasetState(well_paths=paths)
    ds.begin_load("/d")
    assert paths == {}  # same object, cleared — views holding a ref see it


# ── timepoint rebuild ────────────────────────────────────────────────────────

def test_rebuild_timepoints_from_cache():
    ds = DatasetState(cache={"A01": _df([0.0, 24.0, 48.0])})
    ds.rebuild_timepoints()
    assert ds.all_timepoints_cache == [0.0, 24.0, 48.0]


def test_rebuild_timepoints_merges_pipeline_info():
    ds = DatasetState(
        cache={"A01": _df([0.0])},
        pipeline_info={"available_timepoints": ["24h"]},
    )
    ds.rebuild_timepoints()
    assert ds.all_timepoints_cache == [0.0, 24.0]


def test_rebuild_timepoints_empty():
    ds = DatasetState()
    ds.rebuild_timepoints()
    assert ds.all_timepoints_cache == []


# ── FOV rebuild ──────────────────────────────────────────────────────────────

def test_rebuild_fovs_from_cache():
    ds = DatasetState(cache={"A01": _df([0.0, 0.0], fovs=["1", "2"])})
    ds.rebuild_fovs()
    assert ds.all_fovs_cache == ["1", "2"]


def test_rebuild_fovs_defaults_to_single_fov():
    df = pd.DataFrame([{"timepoint_hours": 0.0, "gfp_mean_intensity": 1.0}])
    ds = DatasetState(cache={"A01": df}, well_paths={"A01": "x"})
    ds.rebuild_fovs()
    assert ds.all_fovs_cache == ["1"]


def test_rebuild_fovs_empty_when_no_data():
    ds = DatasetState()
    ds.rebuild_fovs()
    assert ds.all_fovs_cache == []
