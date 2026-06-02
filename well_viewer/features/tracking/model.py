"""Track data model — DataFrame ⇄ engine I/O and the tracks.json payload (Qt-free).

Bridges the per-cell CSV (centroid columns from §C) and the engine adapter
types, and builds the versioned ``tracks.json`` sidecar. The three track columns
are **additive and optional** — pre-tracking datasets simply lack them.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from well_viewer.data.data_loading import parse_timepoint_hours
from .engines.base import Detection, FrameDetections, TrackResult, UNTRACKED

TRACKS_JSON_VERSION = 1

# Centroid columns emitted by the §C shape-feature pass (always-on).
CENTROID_ROW_COL = "shape_centroid_row"
CENTROID_COL_COL = "shape_centroid_col"

TRACK_ID_COL = "track_id"
TRACK_PARENT_COL = "track_parent_id"
TREE_ID_COL = "tree_id"
TRACK_COLUMNS = (TRACK_ID_COL, TRACK_PARENT_COL, TREE_ID_COL)


def _tp_order(timepoints) -> List[str]:
    """Distinct timepoints in time order (parsed hours; string fallback)."""
    uniq = list(dict.fromkeys(str(t) for t in timepoints))

    def key(t: str):
        h = parse_timepoint_hours(t)
        return (0, h) if h is not None else (1, t)

    return sorted(uniq, key=key)


def build_frames_from_df(
    df: pd.DataFrame,
    *,
    tp_col: str = "timepoint",
    nid_col: str = "nucleus_id",
    row_col: str = CENTROID_ROW_COL,
    col_col: str = CENTROID_COL_COL,
) -> "Tuple[List[FrameDetections], Dict[str, int]]":
    """Assemble ordered :class:`FrameDetections` from one (well, fov) DataFrame.

    Returns ``(frames, tp_to_frame)``. Requires the centroid columns; raises
    ``KeyError`` if they are absent (tracking needs §C centroids).
    """
    for col in (tp_col, nid_col, row_col, col_col):
        if col not in df.columns:
            raise KeyError(f"build_frames_from_df: missing required column {col!r}")
    order = _tp_order(df[tp_col].tolist())
    tp_to_frame = {tp: i for i, tp in enumerate(order)}
    frames: List[FrameDetections] = []
    for tp in order:
        sub = df[df[tp_col].astype(str) == tp]
        dets: List[Detection] = []
        for _, r in sub.iterrows():
            row = pd.to_numeric(r[row_col], errors="coerce")
            col = pd.to_numeric(r[col_col], errors="coerce")
            if pd.isna(row) or pd.isna(col):
                continue
            dets.append(Detection(nid=int(r[nid_col]), row=float(row), col=float(col)))
        frames.append(FrameDetections(frame=tp_to_frame[tp], timepoint=tp, detections=dets))
    return frames, tp_to_frame


def assign_track_columns(
    df: pd.DataFrame,
    result: TrackResult,
    tp_to_frame: Dict[str, int],
    *,
    tp_col: str = "timepoint",
    nid_col: str = "nucleus_id",
) -> pd.DataFrame:
    """Add/overwrite the three track columns on *df* (in place) from *result*.

    Rows the engine didn't link get :data:`UNTRACKED` (-1). Returns *df*.
    """
    tids, parents, trees = [], [], []
    for _, r in df.iterrows():
        frame = tp_to_frame.get(str(r[tp_col]))
        if frame is None:
            tids.append(UNTRACKED); parents.append(UNTRACKED); trees.append(UNTRACKED)
            continue
        try:
            nid = int(r[nid_col])
        except (TypeError, ValueError):
            tids.append(UNTRACKED); parents.append(UNTRACKED); trees.append(UNTRACKED)
            continue
        tid = result.track_id_for(frame, nid)
        tids.append(tid)
        parents.append(result.parent_of(tid) if tid != UNTRACKED else UNTRACKED)
        trees.append(result.tree_of(tid) if tid != UNTRACKED else UNTRACKED)
    df[TRACK_ID_COL] = tids
    df[TRACK_PARENT_COL] = parents
    df[TREE_ID_COL] = trees
    return df


def tracks_json_payload(
    *,
    well: str,
    groups: "List[Tuple[str, List[FrameDetections], TrackResult]]",
    engine: str,
    params: dict,
    fingerprints: "Dict[str, str] | None" = None,
) -> dict:
    """Build the ``tracks.json`` lineage-graph payload for one well.

    *groups* is a list of ``(fov, frames, result)``. Nodes carry the
    ``(well, fov, timepoint, nid)`` identity + frame + centroid; ``tracks`` maps
    ``track_id`` → ordered node list; ``lineage`` maps ``track_id`` → parent.
    """
    nodes: List[dict] = []
    tracks: "Dict[int, List[list]]" = {}
    lineage: Dict[int, int] = {}
    trees: Dict[int, int] = {}

    for fov, frames, result in groups:
        centroid = {(f.frame, d.nid): (d.row, d.col) for f in frames for d in f.detections}
        tp_of = {f.frame: f.timepoint for f in frames}
        for (frame, nid), tid in sorted(result.assignments.items()):
            r, c = centroid.get((frame, nid), (None, None))
            nodes.append({
                "well": well, "fov": fov, "timepoint": tp_of.get(frame, ""),
                "nid": int(nid), "frame": int(frame),
                "row": r, "col": c, "track_id": int(tid),
            })
            tracks.setdefault(int(tid), []).append([fov, int(frame), int(nid)])
        for tid, parent in result.parents.items():
            lineage[int(tid)] = int(parent)
        for tid, tree in result.tree_ids.items():
            trees[int(tid)] = int(tree)

    return {
        "version": TRACKS_JSON_VERSION,
        "well": well,
        "engine": engine,
        "params": dict(params or {}),
        "fingerprints": dict(fingerprints or {}),
        "nodes": nodes,
        "tracks": {str(k): v for k, v in tracks.items()},
        "lineage": {str(k): v for k, v in lineage.items()},
        "trees": {str(k): v for k, v in trees.items()},
    }
