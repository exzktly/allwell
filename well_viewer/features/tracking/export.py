"""Track export writers (Qt-free, §D / PR5d).

Three on-disk formats from a tracked per-cell DataFrame:

* **Motility summary CSV** — one row per track (the :mod:`motility` metrics).
* **CTC** ``man_track.txt`` — the Cell Tracking Challenge lineage table
  (``L B E P``: label, begin frame, end frame, parent label).
* **Lineage JSON** — the :func:`model.payload_from_df` graph (nodes/tracks/lineage).

All take a DataFrame + a path and return the path written, so the Analytics
sub-tab's export buttons and tests share one implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from well_viewer.data.data_loading import parse_timepoint_hours
from .model import TRACK_ID_COL, TRACK_PARENT_COL, payload_from_df
from .motility import compute_track_motility


def export_motility_csv(df: pd.DataFrame, path, *, tp_col: str = "timepoint",
                        fov_col: str = "fov") -> Path:
    """Write the per-track motility summary to *path* (CSV)."""
    path = Path(path)
    compute_track_motility(df, tp_col=tp_col, fov_col=fov_col).to_csv(path, index=False)
    return path


def export_ctc_man_track(df: pd.DataFrame, path, *, tp_col: str = "timepoint") -> Path:
    """Write a CTC ``man_track.txt`` (``L B E P`` per track).

    Frame indices are 0-based ordinals over the dataset's sorted timepoints;
    ``P`` is the parent track label (0 = no parent).
    """
    path = Path(path)
    lines = []
    if df is not None and not df.empty and TRACK_ID_COL in df.columns:
        order = sorted(
            {str(t) for t in df[tp_col]} if tp_col in df.columns else set(),
            key=lambda t: (parse_timepoint_hours(t) if parse_timepoint_hours(t) is not None else 0.0),
        )
        frame_of = {tp: i for i, tp in enumerate(order)}
        tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
        valid = df[tid.notna() & (tid >= 0)]
        for track_id, grp in valid.groupby(pd.to_numeric(valid[TRACK_ID_COL], errors="coerce")):
            frames = [frame_of.get(str(t), 0) for t in grp[tp_col]] if tp_col in grp.columns else [0]
            begin, end = min(frames), max(frames)
            parent = 0
            if TRACK_PARENT_COL in grp.columns:
                p = pd.to_numeric(grp[TRACK_PARENT_COL], errors="coerce")
                pv = int(p.iloc[0]) if len(p) and pd.notna(p.iloc[0]) else -1
                parent = pv if pv > 0 else 0
            lines.append(f"{int(track_id)} {begin} {end} {parent}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return path


def export_lineage_json(df: pd.DataFrame, path, *, well: str = "") -> Path:
    """Write the lineage graph (``payload_from_df``) to *path* (JSON)."""
    path = Path(path)
    payload = payload_from_df(well or "", df, engine="export")
    path.write_text(json.dumps(payload, indent=2))
    return path
