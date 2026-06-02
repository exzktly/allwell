"""Tracking analytics: per-track motility metrics + export writers (§D / PR5d)."""

from __future__ import annotations

import json

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.tracking.motility import compute_track_motility
from well_viewer.features.tracking import export


def _straight_track():
    # one track moving in a straight line +2 row,+0 col each of 4 timepoints
    rows = []
    for i, tp in enumerate(("0", "1", "2", "3")):
        rows.append(dict(well="A01", fov="F001", timepoint=tp, nucleus_id=1,
                         shape_centroid_row=10 + 2 * i, shape_centroid_col=10,
                         track_id=1, track_parent_id=-1, tree_id=1))
    return pd.DataFrame(rows)


def _zigzag_track():
    pts = [(10, 10), (12, 12), (10, 14), (12, 16)]  # zig-zag
    rows = []
    for i, ((r, c), tp) in enumerate(zip(pts, ("0", "1", "2", "3"))):
        rows.append(dict(well="A01", fov="F001", timepoint=tp, nucleus_id=1,
                         shape_centroid_row=r, shape_centroid_col=c,
                         track_id=7, track_parent_id=-1, tree_id=7))
    return pd.DataFrame(rows)


# ── motility ─────────────────────────────────────────────────────────────────

def test_motility_straight_line():
    m = compute_track_motility(_straight_track())
    assert len(m) == 1
    row = m.iloc[0]
    assert row["track_id"] == 1
    assert row["n_frames"] == 4
    assert row["total_distance"] == pytest.approx(6.0)       # 3 steps × 2
    assert row["net_displacement"] == pytest.approx(6.0)
    assert row["directionality"] == pytest.approx(1.0)       # perfectly directed
    assert row["duration_h"] == pytest.approx(3.0)
    assert row["mean_speed"] == pytest.approx(2.0)
    assert row["mean_turning_angle_deg"] == pytest.approx(0.0, abs=1e-6)


def test_motility_zigzag_low_directionality():
    m = compute_track_motility(_zigzag_track())
    row = m.iloc[0]
    assert row["total_distance"] > row["net_displacement"]
    assert 0.0 < row["directionality"] < 1.0
    assert row["mean_turning_angle_deg"] > 30.0               # zig-zag turns


def test_motility_per_fov_separation():
    a = _straight_track()
    b = _straight_track()
    b["fov"] = "F002"
    m = compute_track_motility(pd.concat([a, b], ignore_index=True))
    assert len(m) == 2 and set(m["fov"]) == {"F001", "F002"}


def test_motility_empty_or_untracked():
    assert compute_track_motility(pd.DataFrame()).empty
    df = _straight_track()
    df["track_id"] = -1
    assert compute_track_motility(df).empty


# ── export ───────────────────────────────────────────────────────────────────

def test_export_motility_csv(tmp_path):
    p = export.export_motility_csv(_straight_track(), tmp_path / "motility.csv")
    out = pd.read_csv(p)
    assert "directionality" in out.columns and len(out) == 1


def test_export_ctc_man_track(tmp_path):
    df = _straight_track()
    # a second track that divides off track 1
    div = _straight_track()
    div["track_id"] = 2
    div["track_parent_id"] = 1
    df = pd.concat([df, div], ignore_index=True)
    p = export.export_ctc_man_track(df, tmp_path / "man_track.txt")
    lines = [ln.split() for ln in p.read_text().splitlines()]
    by_label = {int(l[0]): l for l in lines}
    assert by_label[1] == ["1", "0", "3", "0"]   # L=1 begin0 end3 parent0
    assert by_label[2][3] == "1"                  # track 2's parent is 1


def test_export_lineage_json(tmp_path):
    p = export.export_lineage_json(_straight_track(), tmp_path / "lineage.json", well="A01")
    data = json.loads(p.read_text())
    assert data["well"] == "A01" and "1" in data["tracks"]
