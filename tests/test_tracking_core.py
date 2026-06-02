"""Cell-tracking core (§D / PR5a): greedy engine, orchestrator, persistence.

Qt-free; needs numpy/scipy/pandas. The greedy engine is dependency-free so the
linking logic is fully exercised here; the btrack adapter is guard-tested."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("scipy")
pd = pytest.importorskip("pandas")

from well_viewer.features.tracking.engines import (
    DEFAULT_ENGINE, EngineParams, run_tracking, available_engines, all_engines,
)
from well_viewer.features.tracking.engines.base import Detection, FrameDetections, UNTRACKED
from well_viewer.features.tracking.orchestrator import track_well, track_dataframe


def _frames(tracks_xy):
    """tracks_xy: list of frames; each frame = list of (nid, row, col) or None gap."""
    frames = []
    for fi, dets in enumerate(tracks_xy):
        frames.append(FrameDetections(
            frame=fi, timepoint=f"T{fi:02d}",
            detections=[Detection(nid=n, row=r, col=c) for (n, r, c) in dets]))
    return frames


# ── greedy engine ────────────────────────────────────────────────────────────

def test_greedy_links_two_cells():
    frames = _frames([
        [(1, 10, 10), (2, 50, 50)],
        [(1, 12, 11), (2, 52, 49)],
        [(1, 14, 12), (2, 54, 48)],
    ])
    res = run_tracking("greedy", frames, EngineParams(max_distance=10))
    assert res.n_tracks == 2
    # each cell keeps one track id across all three frames
    a = {res.track_id_for(f, 1) for f in range(3)}
    b = {res.track_id_for(f, 2) for f in range(3)}
    assert len(a) == 1 and len(b) == 1 and a != b


def test_greedy_distance_gate_starts_new_track():
    # a cell jumps 100 px between frames → exceeds max_distance → new track
    frames = _frames([[(1, 10, 10)], [(1, 10, 120)]])
    res = run_tracking("greedy", frames, EngineParams(max_distance=20))
    assert res.n_tracks == 2


def test_greedy_gap_closing():
    frames = _frames([[(1, 10, 10)], [], [(1, 14, 12)]])  # missing in middle frame
    linked = run_tracking("greedy", frames, EngineParams(max_distance=20, max_gap_frames=1))
    assert linked.n_tracks == 1
    unlinked = run_tracking("greedy", frames, EngineParams(max_distance=20, max_gap_frames=0))
    assert unlinked.n_tracks == 2


# ── orchestrator over a DataFrame ────────────────────────────────────────────

def _well_df(fov="F001"):
    rows = []
    for tp, (ax, bx) in zip(("0", "24h", "48h"), [(10, 50), (12, 52), (14, 54)]):
        rows.append(dict(well="A01", fov=fov, timepoint=tp, nucleus_id=1,
                         shape_centroid_row=ax, shape_centroid_col=ax))
        rows.append(dict(well="A01", fov=fov, timepoint=tp, nucleus_id=2,
                         shape_centroid_row=bx, shape_centroid_col=bx))
    return pd.DataFrame(rows)


def test_track_well_adds_columns_and_payload():
    df, payload = track_well(_well_df(), "A01", engine="greedy",
                             params=EngineParams(max_distance=10))
    for col in ("track_id", "track_parent_id", "tree_id"):
        assert col in df.columns
    assert set(df["track_id"]) == {1, 2}            # two cells, two tracks
    # each cell's rows all share one track id
    assert df[df["nucleus_id"] == 1]["track_id"].nunique() == 1
    # payload shape
    assert payload["version"] == 1 and payload["well"] == "A01"
    assert payload["engine"] == "greedy"
    assert len(payload["nodes"]) == 6
    assert set(payload["tracks"].keys()) == {"1", "2"}


def test_track_dataframe_offsets_fov_track_ids():
    df = pd.concat([_well_df("F001"), _well_df("F002")], ignore_index=True)
    out, groups = track_dataframe(df, engine="greedy", params=EngineParams(max_distance=10))
    # 2 FOVs × 2 cells → 4 distinct positive track ids, no collisions
    pos = sorted(set(out[out["track_id"] != UNTRACKED]["track_id"]))
    assert pos == [1, 2, 3, 4]
    assert len(groups) == 2


# ── persistence (save/load + staleness) ──────────────────────────────────────

def test_persistence_roundtrip_and_staleness(tmp_path):
    from well_viewer.persistence import tracking as tp
    payload = {"version": 1, "well": "A01", "engine": "greedy",
               "fingerprints": {"A01": "FP1"}, "nodes": [], "tracks": {}, "lineage": {}}
    tp.save_tracks(tmp_path, {"A01": payload})
    assert (tmp_path / "tracks.json").exists()

    # matching fingerprint → kept
    assert "A01" in tp.load_tracks(tmp_path, current_fingerprints={"A01": "FP1"})
    # changed fingerprint → dropped (pipeline re-ran)
    assert tp.load_tracks(tmp_path, current_fingerprints={"A01": "FP2"}) == {}
    # no check → returned
    assert "A01" in tp.load_tracks(tmp_path)
    # missing file → {}
    assert tp.load_tracks(tmp_path / "nope") == {}


# ── engine registry / guards ─────────────────────────────────────────────────

def test_engine_registry():
    names = {e.name for e in all_engines()}
    assert "greedy" in names and "btrack" in names
    assert DEFAULT_ENGINE == "greedy"
    assert any(e.name == "greedy" for e in available_engines())  # always available
    with pytest.raises(KeyError):
        run_tracking("does_not_exist", [], EngineParams())


def test_btrack_guarded():
    import importlib.util
    if importlib.util.find_spec("btrack") is not None:
        pytest.skip("btrack installed")
    with pytest.raises(RuntimeError, match="btrack"):
        run_tracking("btrack", _frames([[(1, 0, 0)]]), EngineParams())
