"""Per-track motility metrics (Qt-free, §D / PR5d).

Computes single-cell migration statistics from a tracked per-cell DataFrame
(``track_id`` + the §C centroid columns), one row per track. Pure numpy/pandas
so it serves both the Analytics sub-tab's local plots and the export writers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from well_viewer.data.data_loading import parse_timepoint_hours
from .model import TRACK_ID_COL, CENTROID_ROW_COL, CENTROID_COL_COL

MOTILITY_COLUMNS = (
    "track_id", "fov", "n_frames", "duration_h", "total_distance",
    "net_displacement", "directionality", "mean_speed", "max_speed",
    "msd", "mean_turning_angle_deg",
)


def _ordered(grp: pd.DataFrame, tp_col: str) -> pd.DataFrame:
    if tp_col not in grp.columns:
        return grp
    keys = grp[tp_col].map(lambda t: parse_timepoint_hours(str(t)))
    keys = keys.fillna(0.0)
    return grp.assign(_t=keys).sort_values("_t")


def _turning_angles_deg(r: np.ndarray, c: np.ndarray) -> float:
    """Mean turning angle (degrees) between consecutive step vectors."""
    if r.size < 3:
        return 0.0
    steps = np.stack([np.diff(r), np.diff(c)], axis=1)  # (n-1, 2)
    norms = np.linalg.norm(steps, axis=1)
    angs = []
    for i in range(len(steps) - 1):
        a, b = steps[i], steps[i + 1]
        na, nb = norms[i], norms[i + 1]
        if na <= 0 or nb <= 0:
            continue
        cosang = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
        angs.append(np.degrees(np.arccos(cosang)))
    return float(np.mean(angs)) if angs else 0.0


def _track_metrics(grp: pd.DataFrame, *, tp_col: str) -> "dict | None":
    g = _ordered(grp, tp_col)
    r = pd.to_numeric(g[CENTROID_ROW_COL], errors="coerce").to_numpy()
    c = pd.to_numeric(g[CENTROID_COL_COL], errors="coerce").to_numpy()
    keep = np.isfinite(r) & np.isfinite(c)
    r, c = r[keep], c[keep]
    if r.size < 1:
        return None
    times = None
    if tp_col in g.columns:
        times = g[tp_col].map(lambda t: parse_timepoint_hours(str(t)))
        times = pd.to_numeric(times, errors="coerce").to_numpy()[keep]
    steps = np.sqrt(np.diff(r) ** 2 + np.diff(c) ** 2) if r.size > 1 else np.array([])
    total = float(np.nansum(steps))
    net = float(np.hypot(r[-1] - r[0], c[-1] - c[0])) if r.size > 1 else 0.0
    duration = 0.0
    if times is not None and np.isfinite(times).any():
        ft = times[np.isfinite(times)]
        duration = float(ft[-1] - ft[0]) if ft.size > 1 else 0.0
    mean_speed = (total / duration) if duration > 0 else 0.0
    # per-step speed (distance / dt) for max_speed when we have timing
    max_speed = 0.0
    if times is not None and steps.size:
        dt = np.diff(times)
        with np.errstate(divide="ignore", invalid="ignore"):
            sp = np.where(dt > 0, steps / dt, np.nan)
        if np.isfinite(sp).any():
            max_speed = float(np.nanmax(sp))
    # MSD: mean squared displacement from the origin across frames
    msd = float(np.mean((r - r[0]) ** 2 + (c - c[0]) ** 2)) if r.size > 1 else 0.0
    return {
        "n_frames": int(r.size),
        "duration_h": duration,
        "total_distance": total,
        "net_displacement": net,
        "directionality": (net / total) if total > 0 else 0.0,
        "mean_speed": mean_speed,
        "max_speed": max_speed,
        "msd": msd,
        "mean_turning_angle_deg": _turning_angles_deg(r, c),
    }


def compute_track_motility(
    df: pd.DataFrame,
    *,
    tp_col: str = "timepoint",
    fov_col: str = "fov",
) -> pd.DataFrame:
    """One row of motility metrics per ``track_id`` (empty frame if untracked).

    Tracks are grouped within ``(fov, track_id)`` (cells are linked per-FOV), so
    the same numeric ``track_id`` in two FOVs stays separate.
    """
    if df is None or df.empty or TRACK_ID_COL not in df.columns:
        return pd.DataFrame(columns=list(MOTILITY_COLUMNS))
    if CENTROID_ROW_COL not in df.columns or CENTROID_COL_COL not in df.columns:
        return pd.DataFrame(columns=list(MOTILITY_COLUMNS))
    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    valid = df[tid.notna() & (tid >= 0)]
    has_fov = fov_col in valid.columns
    rows = []
    group_cols = ([fov_col, TRACK_ID_COL] if has_fov else [TRACK_ID_COL])
    for key, grp in valid.groupby(group_cols):
        if has_fov:
            fov, track_id = key
        else:
            fov, track_id = "1", key
        m = _track_metrics(grp, tp_col=tp_col)
        if m is None:
            continue
        m["track_id"] = int(track_id)
        m["fov"] = str(fov)
        rows.append(m)
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=list(MOTILITY_COLUMNS))
    return out[list(MOTILITY_COLUMNS)].sort_values(["fov", "track_id"]).reset_index(drop=True)
