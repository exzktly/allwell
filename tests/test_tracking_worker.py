"""Tracking post-pass worker (§D / PR5b): CSV write-back + tracks.json."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("scipy")
pd = pytest.importorskip("pandas")

from well_viewer.features.tracking.engines import EngineParams
from well_viewer.features.tracking.worker import write_tracks_to_csvs, track_wells_async


def _write_well_csv(path, *, with_centroids=True):
    rows = []
    for tp, (a, b) in zip(("0", "24h", "48h"), [(10, 50), (12, 52), (14, 54)]):
        for nid, x in ((1, a), (2, b)):
            row = dict(well="A01", fov="F001", timepoint=tp, nucleus_id=nid,
                       gfp_mean_intensity=100 + nid)
            if with_centroids:
                row["shape_centroid_row"] = x
                row["shape_centroid_col"] = x
            rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_write_tracks_to_csvs(tmp_path):
    csv = tmp_path / "measurements_A01.csv"
    _write_well_csv(csv)

    tracks = write_tracks_to_csvs(
        tmp_path, {"A01": csv}, params=EngineParams(max_distance=10))

    # CSV gained the three track columns
    df = pd.read_csv(csv)
    for col in ("track_id", "track_parent_id", "tree_id"):
        assert col in df.columns
    assert set(df["track_id"]) == {1, 2}
    assert df[df["nucleus_id"] == 1]["track_id"].nunique() == 1
    # tracks.json written + payload returned
    assert (tmp_path / "tracks.json").exists()
    assert "A01" in tracks and tracks["A01"]["engine"] == "greedy"


def test_write_tracks_rerun_no_duplicate_columns(tmp_path):
    csv = tmp_path / "measurements_A01.csv"
    _write_well_csv(csv)
    write_tracks_to_csvs(tmp_path, {"A01": csv}, params=EngineParams(max_distance=10))
    write_tracks_to_csvs(tmp_path, {"A01": csv}, params=EngineParams(max_distance=10))
    df = pd.read_csv(csv)
    # exactly one of each track column (no case-duplicate accumulation)
    assert list(df.columns).count("track_id") == 1


def test_legacy_csv_without_centroids_skipped(tmp_path):
    csv = tmp_path / "measurements_A01.csv"
    _write_well_csv(csv, with_centroids=False)
    tracks = write_tracks_to_csvs(tmp_path, {"A01": csv}, params=EngineParams())
    assert tracks == {}                         # nothing tracked
    df = pd.read_csv(csv)
    assert "track_id" not in df.columns         # CSV untouched
    assert not (tmp_path / "tracks.json").exists()


def test_track_wells_async(tmp_path):
    csv = tmp_path / "measurements_A01.csv"
    _write_well_csv(csv)
    msgs, done = [], []
    after = threading.Event()
    th = track_wells_async(
        out_dir=tmp_path, well_to_csv={"A01": csv},
        params=EngineParams(max_distance=10),
        status_cb=msgs.append, done_cb=done.append,
        after_csv_cb=after.set,
    )
    th.join(timeout=30)
    assert not th.is_alive()
    assert after.is_set()
    assert done and "complete" in done[-1].lower()
    assert "track_id" in pd.read_csv(csv).columns


# ── Analyze → CLI arg mapping + the Cell Tracking section ─────────────────────

def test_build_pipeline_args_track_mapping():
    from pathlib import Path
    from services.pipeline_service import build_pipeline_args
    base = dict(nuclear_token="NIR", fluor_tokens=["GFP"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                segmentation_method="stardist_nuclei", min_nucleus_area_px=50,
                tf_threads="0", workers="0", compress_input_well_folders=True,
                compress_output_well_folders=True)
    # tracking off → no flags
    a = build_pipeline_args(Path("/x/p.py"), Path("/in"), Path("/out"), {**base, "track": False})
    assert "--track" not in a
    # tracking on → engine + params
    a = build_pipeline_args(Path("/x/p.py"), Path("/in"), Path("/out"), {
        **base, "track": True, "track_engine": "btrack", "track_max_distance": 25.0,
        "track_max_gap": 2, "track_allow_division": True})
    assert "--track" in a
    assert a[a.index("--track_engine") + 1] == "btrack"
    assert a[a.index("--track_max_distance") + 1] == "25.0"
    assert a[a.index("--track_max_gap") + 1] == "2"
    assert "--track_allow_division" in a


def test_analyze_tracking_section():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    engines = [tab._track_engine_cb.itemText(i) for i in range(tab._track_engine_cb.count())]
    assert "greedy" in engines and "btrack" in engines
    # controls disabled until enabled
    assert tab._track_engine_cb.isEnabled() is False
    tab._track_cb.setChecked(True)
    assert tab._track_engine_cb.isEnabled() is True
    tab._track_max_dist_edit.setText("25")
    opts = tab._collect_run_options()
    assert opts["track"] is True and opts["track_engine"] == "greedy"
    assert opts["track_max_distance"] == 25.0
