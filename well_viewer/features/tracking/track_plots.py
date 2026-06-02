"""Per-track time-series for the Track Plots sub-tab (Qt-free, §D / PR5d).

Builds, from one well's tracked DataFrame, the per-track ``(time, metric)``
series plus the well's **average track** (the per-timepoint mean across that
well's tracks). The Track Plots view colours each well distinctly and overlays
its average; this module is the testable core behind it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import pandas as pd

from well_viewer.data.data_loading import parse_timepoint_hours
from .model import TRACK_ID_COL, CENTROID_ROW_COL, CENTROID_COL_COL

# Structural / identity columns that aren't plottable per-cell metrics.
_NON_METRIC = {
    "nucleus_id", TRACK_ID_COL, "track_parent_id", "tree_id", "included",
    "timepoint_hours", CENTROID_ROW_COL, CENTROID_COL_COL,
    "well", "fov", "timepoint", "channel", "experiment", "filename",
}


def plottable_metrics(df: pd.DataFrame) -> List[str]:
    """Numeric per-cell columns that can be plotted over time (shape features,
    intensities, …), in DataFrame order. Excludes identity/position columns."""
    if df is None or df.empty:
        return []
    out: List[str] = []
    for c in df.columns:
        if str(c).strip().lower() in _NON_METRIC:
            continue
        if pd.to_numeric(df[c], errors="coerce").notna().any():
            out.append(c)
    return out


@dataclass
class TrackSeries:
    """Per-track series + the well's average track for one metric."""
    tracks: List[Tuple[int, np.ndarray, np.ndarray]] = field(default_factory=list)
    avg_times: np.ndarray = field(default_factory=lambda: np.array([]))
    avg_values: np.ndarray = field(default_factory=lambda: np.array([]))

    @property
    def n_tracks(self) -> int:
        return len(self.tracks)


def compute_track_series(
    df: pd.DataFrame,
    metric: str,
    *,
    tp_col: str = "timepoint",
    fov_col: str = "fov",
) -> TrackSeries:
    """Per-track ``(times_h, values)`` for *metric* + the well's average track.

    Tracks are grouped within ``(fov, track_id)``; each series is time-ordered.
    The average is the per-timepoint mean of *metric* across all of the well's
    tracks. Empty when there are no tracks or the metric is absent.
    """
    out = TrackSeries()
    if (df is None or df.empty or TRACK_ID_COL not in df.columns
            or metric not in df.columns or tp_col not in df.columns):
        return out
    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    valid = df[tid.notna() & (tid >= 0)]
    if valid.empty:
        return out
    group_cols = [fov_col, TRACK_ID_COL] if fov_col in valid.columns else [TRACK_ID_COL]
    per_time: "dict[float, list]" = defaultdict(list)
    for key, grp in valid.groupby(group_cols):
        track_id = key[-1] if isinstance(key, tuple) else key
        t = pd.to_numeric(grp[tp_col].map(lambda x: parse_timepoint_hours(str(x))),
                          errors="coerce").to_numpy()
        v = pd.to_numeric(grp[metric], errors="coerce").to_numpy()
        keep = np.isfinite(t) & np.isfinite(v)
        t, v = t[keep], v[keep]
        if t.size == 0:
            continue
        order = np.argsort(t)
        t, v = t[order], v[order]
        out.tracks.append((int(track_id), t, v))
        for tt, vv in zip(t, v):
            per_time[float(tt)].append(float(vv))
    if per_time:
        times = sorted(per_time)
        out.avg_times = np.array(times)
        out.avg_values = np.array([float(np.mean(per_time[tt])) for tt in times])
    return out
