"""Track editing (undo) + rule-based pruning cores (§D / PR5c)."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.tracking.edit import TrackEditor
from well_viewer.features.tracking import pruning
from well_viewer.features.tracking.engines.base import UNTRACKED


def _df():
    # two tracks over 3 timepoints (cells 1 & 2), plus a 1-frame blob (cell 3)
    rows = []
    for tp, r1, r2 in (("0", 10, 50), ("24h", 12, 52), ("48h", 14, 54)):
        rows.append(dict(timepoint=tp, nucleus_id=1, shape_centroid_row=r1,
                         shape_centroid_col=r1, track_id=1, track_parent_id=-1, tree_id=1))
        rows.append(dict(timepoint=tp, nucleus_id=2, shape_centroid_row=r2,
                         shape_centroid_col=r2, track_id=2, track_parent_id=-1, tree_id=2))
    rows.append(dict(timepoint="0", nucleus_id=3, shape_centroid_row=80,
                     shape_centroid_col=80, track_id=3, track_parent_id=-1, tree_id=3))
    return pd.DataFrame(rows)


# ── editor ───────────────────────────────────────────────────────────────────

def test_relabel_and_undo():
    df = _df()
    ed = TrackEditor(df)
    assert ed.relabel(1, 9) == 3
    assert set(df[df["nucleus_id"] == 1]["track_id"]) == {9}
    assert ed.can_undo() and ed.undo()
    assert set(df[df["nucleus_id"] == 1]["track_id"]) == {1}


def test_merge():
    df = _df()
    ed = TrackEditor(df)
    moved = ed.merge(1, 2)        # track 2 → track 1
    assert moved == 3
    assert 2 not in ed.track_ids()
    assert (df["track_id"] == 1).sum() == 6


def test_delete():
    df = _df()
    ed = TrackEditor(df)
    assert ed.delete(3) == 1
    assert (df["track_id"] == UNTRACKED).sum() == 1
    assert 3 not in ed.track_ids()


def test_split():
    df = _df()
    ed = TrackEditor(df)
    new_id = ed.split(1, "24h")   # cells at 24h and 48h become a new track
    assert new_id == ed.track_ids()[-1]
    early = df[(df["nucleus_id"] == 1) & (df["timepoint"] == "0")]["track_id"].iloc[0]
    late = df[(df["nucleus_id"] == 1) & (df["timepoint"] == "48h")]["track_id"].iloc[0]
    assert early == 1 and late == new_id
    # cannot split where one side is empty
    assert ed.split(2, "0") is None


def test_assign_and_clear_division():
    df = _df()
    ed = TrackEditor(df)
    assert ed.assign_division(parent_id=1, child_id=2)
    assert set(df[df["track_id"] == 2]["track_parent_id"]) == {1}
    assert set(df[df["track_id"] == 2]["tree_id"]) == {1}     # joins parent's tree
    assert ed.clear_division(2)
    assert set(df[df["track_id"] == 2]["track_parent_id"]) == {-1}


# ── pruning ──────────────────────────────────────────────────────────────────

def test_prune_min_frames():
    df = _df()
    rules = pruning.PruneRules(min_frames=2)   # the 1-frame blob (track 3) goes
    assert pruning.evaluate(df, rules) == {3}
    n_tracks, n_cells = pruning.preview(df, rules)
    assert (n_tracks, n_cells) == (1, 1)
    assert pruning.apply(df, {3}) == 1
    assert (df["track_id"] == UNTRACKED).sum() == 1


def test_prune_min_displacement():
    df = _df()
    # tracks 1 & 2 move ~5.6 px total; require >100 → both dropped, blob too
    rules = pruning.PruneRules(min_displacement=100.0)
    assert pruning.evaluate(df, rules) == {1, 2, 3}
    # a gentle floor keeps the moving tracks
    assert pruning.evaluate(df, pruning.PruneRules(min_displacement=1.0)) == {3}


def test_prune_inactive_rules_noop():
    df = _df()
    assert pruning.evaluate(df, pruning.PruneRules()) == set()
    assert pruning.preview(df, pruning.PruneRules()) == (0, 0)


def test_payload_from_df_rebuild():
    from well_viewer.features.tracking.model import payload_from_df
    df = _df()
    df.loc[df["track_id"] == 2, "track_parent_id"] = 1   # track 2 is a division child of 1
    df.loc[df["track_id"] == 2, "tree_id"] = 1
    payload = payload_from_df("A01", df)
    assert payload["well"] == "A01" and payload["engine"] == "manual"
    assert set(payload["tracks"]) >= {"1", "2", "3"}
    assert payload["lineage"]["2"] == 1
    assert len(payload["nodes"]) == int((df["track_id"] >= 0).sum())
