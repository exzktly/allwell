"""Greedy nearest-neighbour LAP tracker — the dependency-free default engine.

Pure numpy + ``scipy.optimize.linear_sum_assignment``: solves an optimal
one-to-one assignment between consecutive frames using Euclidean centroid
distance, gated by ``max_distance``. Unmatched detections start new tracks;
optional gap closing re-links a track that vanished for up to ``max_gap_frames``
frames. No model download, instant on typical well sizes — this fills the
"lightweight default" role (laptrack was dropped from the in-core dependency
set; see the foundation PR). Division is not modelled (use btrack for lineage).
"""

from __future__ import annotations

from typing import List

import numpy as np

from .base import EngineParams, FrameDetections, TrackResult

NAME = "greedy"
DISPLAY_NAME = "Greedy (nearest-neighbour)"
AVAILABLE = True
REQUIRES = ()


def _coords(frame: FrameDetections) -> "tuple[np.ndarray, list[int]]":
    if not frame.detections:
        return np.zeros((0, 2), dtype=float), []
    pts = np.array([[d.row, d.col] for d in frame.detections], dtype=float)
    nids = [int(d.nid) for d in frame.detections]
    return pts, nids


def run(frames: List[FrameDetections], params: EngineParams) -> TrackResult:
    from scipy.optimize import linear_sum_assignment

    result = TrackResult()
    if not frames:
        return result.finalize()

    frames = sorted(frames, key=lambda f: f.frame)
    max_d = float(params.max_distance)
    max_gap = max(0, int(params.max_gap_frames))

    next_track_id = 1
    # Active track tails available for linking: track_id -> (last_frame, row, col)
    active: "dict[int, tuple[int, float, float]]" = {}

    for fi, frame in enumerate(frames):
        pts, nids = _coords(frame)
        if pts.shape[0] == 0:
            continue

        # Candidate tails still within the gap horizon.
        tail_ids = [tid for tid, (lf, _r, _c) in active.items()
                    if frame.frame - lf <= max_gap + 1]
        assigned_det = set()

        if tail_ids:
            tails = np.array([active[t][1:] for t in tail_ids], dtype=float)
            # cost = distance; gate by max_distance with a large finite penalty.
            d = np.linalg.norm(tails[:, None, :] - pts[None, :, :], axis=2)
            BIG = max_d * 1000.0 + 1.0
            cost = np.where(d <= max_d, d, BIG)
            ti, dj = linear_sum_assignment(cost)
            for r, c in zip(ti, dj):
                if cost[r, c] >= BIG:
                    continue  # gated out — not a real link
                tid = tail_ids[r]
                result.assignments[(frame.frame, nids[c])] = tid
                active[tid] = (frame.frame, float(pts[c, 0]), float(pts[c, 1]))
                assigned_det.add(c)

        # Unmatched detections open new tracks.
        for c in range(pts.shape[0]):
            if c in assigned_det:
                continue
            tid = next_track_id
            next_track_id += 1
            result.assignments[(frame.frame, nids[c])] = tid
            active[tid] = (frame.frame, float(pts[c, 0]), float(pts[c, 1]))

    return result.finalize()
