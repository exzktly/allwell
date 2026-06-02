"""Rule-based track pruning / QC (Qt-free, §D / PR5c).

Bulk-cleanup of spurious tracks: drop tracks shorter than a minimum number of
frames, or whose total centroid displacement is below a floor (jittering debris).
:func:`evaluate` returns the offending ``track_id`` set (for a preview);
:func:`apply` untracks them (``track_id`` → -1) on the DataFrame. Operates on the
same additive track columns as the engines and the editor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .model import (
    TRACK_ID_COL, TRACK_PARENT_COL, TREE_ID_COL,
    CENTROID_ROW_COL, CENTROID_COL_COL,
)
from .engines.base import UNTRACKED


@dataclass(frozen=True)
class PruneRules:
    """Pruning thresholds. A rule at its no-op default (0) does not fire."""
    min_frames: int = 0            # drop tracks present in < min_frames frames
    min_displacement: float = 0.0  # drop tracks whose path length < this (px)

    @property
    def active(self) -> bool:
        return self.min_frames > 0 or self.min_displacement > 0.0


def _path_length(rows: pd.DataFrame) -> float:
    """Total Euclidean centroid path length across the (time-ordered) rows."""
    if CENTROID_ROW_COL not in rows or CENTROID_COL_COL not in rows or len(rows) < 2:
        return 0.0
    r = pd.to_numeric(rows[CENTROID_ROW_COL], errors="coerce").to_numpy()
    c = pd.to_numeric(rows[CENTROID_COL_COL], errors="coerce").to_numpy()
    d = np.sqrt(np.diff(r) ** 2 + np.diff(c) ** 2)
    return float(np.nansum(d))


def evaluate(df: pd.DataFrame, rules: PruneRules, *, tp_col: str = "timepoint") -> "set[int]":
    """Return the set of ``track_id``s that violate *rules* (would be dropped)."""
    if df is None or df.empty or TRACK_ID_COL not in df.columns or not rules.active:
        return set()
    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    drop: "set[int]" = set()
    for track_id, grp in df[tid.notna() & (tid >= 0)].groupby(tid):
        track_id = int(track_id)
        n_frames = grp[tp_col].nunique() if tp_col in grp.columns else len(grp)
        if rules.min_frames > 0 and n_frames < rules.min_frames:
            drop.add(track_id)
            continue
        if rules.min_displacement > 0.0:
            ordered = grp
            if tp_col in grp.columns:
                from well_viewer.data.data_loading import parse_timepoint_hours
                ordered = grp.assign(_k=grp[tp_col].map(
                    lambda t: parse_timepoint_hours(str(t)) or 0.0)).sort_values("_k")
            if _path_length(ordered) < rules.min_displacement:
                drop.add(track_id)
    return drop


def apply(df: pd.DataFrame, track_ids: "set[int]") -> int:
    """Untrack every cell whose ``track_id`` is in *track_ids*. Returns rows changed."""
    if not track_ids or df is None or df.empty or TRACK_ID_COL not in df.columns:
        return 0
    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    mask = tid.isin(list(track_ids))
    n = int(mask.sum())
    if n:
        df.loc[mask, TRACK_ID_COL] = UNTRACKED
        df.loc[mask, TRACK_PARENT_COL] = UNTRACKED
        df.loc[mask, TREE_ID_COL] = UNTRACKED
    return n


def preview(df: pd.DataFrame, rules: PruneRules, *, tp_col: str = "timepoint") -> "tuple[int, int]":
    """``(n_tracks_dropped, n_cells_affected)`` for *rules* without mutating *df*."""
    drop = evaluate(df, rules, tp_col=tp_col)
    if not drop:
        return 0, 0
    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    return len(drop), int(tid.isin(list(drop)).sum())
