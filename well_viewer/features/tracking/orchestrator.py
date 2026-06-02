"""Engine-agnostic tracking orchestration (Qt-free).

Drives an engine over one well's per-cell DataFrame — tracking each FOV
independently (cells are linked with ``(well, fov)`` held fixed, ``tp`` varying)
— writes the three track columns, and returns the per-FOV groups needed to build
``tracks.json``. Both the GUI post-pass worker (PR5b) and the headless
``--track`` path call this; it imports only numpy/pandas + the engine layer.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from .engines import DEFAULT_ENGINE, EngineParams, FrameDetections, TrackResult, run_tracking
from .engines.base import UNTRACKED
from .model import (
    TRACK_ID_COL, TRACK_PARENT_COL, TREE_ID_COL,
    assign_track_columns, build_frames_from_df, tracks_json_payload,
)


def _offset_result(result: TrackResult, offset: int) -> TrackResult:
    """Shift every positive track id by *offset* so FOVs don't collide."""
    if offset <= 0:
        return result
    out = TrackResult()
    out.assignments = {k: v + offset for k, v in result.assignments.items()}
    out.parents = {k + offset: (v + offset if v != UNTRACKED else UNTRACKED)
                   for k, v in result.parents.items()}
    out.tree_ids = {k + offset: v + offset for k, v in result.tree_ids.items()}
    return out


def track_dataframe(
    df: pd.DataFrame,
    *,
    engine: str = DEFAULT_ENGINE,
    params: Optional[EngineParams] = None,
    fov_col: str = "fov",
    tp_col: str = "timepoint",
    nid_col: str = "nucleus_id",
    image_loader=None,
) -> "Tuple[pd.DataFrame, List[Tuple[str, List[FrameDetections], TrackResult]]]":
    """Track one well's DataFrame in place; return ``(df, groups)``.

    ``groups`` is a list of ``(fov, frames, result)`` for ``tracks_json_payload``.
    Track ids are offset per FOV so they're unique across the whole well.
    """
    if params is None:
        params = EngineParams()
    df[TRACK_ID_COL] = UNTRACKED
    df[TRACK_PARENT_COL] = UNTRACKED
    df[TREE_ID_COL] = UNTRACKED

    if fov_col in df.columns:
        fovs = list(dict.fromkeys(df[fov_col].astype(str)))
    else:
        fovs = ["1"]

    groups: List[Tuple[str, List[FrameDetections], TrackResult]] = []
    offset = 0
    for fov in fovs:
        if fov_col in df.columns:
            sub = df[df[fov_col].astype(str) == fov]
        else:
            sub = df
        frame_loader = (lambda tp, _f=fov: image_loader(_f, tp)) if image_loader else None
        frames, tp_to_frame = build_frames_from_df(
            sub, tp_col=tp_col, nid_col=nid_col, frame_image_loader=frame_loader)
        result = run_tracking(engine, frames, params)
        result = _offset_result(result, offset)
        assigned = assign_track_columns(sub.copy(), result, tp_to_frame,
                                        tp_col=tp_col, nid_col=nid_col)
        df.loc[assigned.index, TRACK_ID_COL] = assigned[TRACK_ID_COL]
        df.loc[assigned.index, TRACK_PARENT_COL] = assigned[TRACK_PARENT_COL]
        df.loc[assigned.index, TREE_ID_COL] = assigned[TREE_ID_COL]
        groups.append((fov, frames, result))
        if result.assignments:
            offset = max(result.assignments.values())
    return df, groups


def track_well(
    df: pd.DataFrame,
    well: str,
    *,
    engine: str = DEFAULT_ENGINE,
    params: Optional[EngineParams] = None,
    fingerprints: "dict | None" = None,
    image_loader=None,
    **cols,
) -> "Tuple[pd.DataFrame, dict]":
    """Track one well and return ``(tracked_df, tracks_json_payload)``."""
    params = params or EngineParams()
    df, groups = track_dataframe(df, engine=engine, params=params,
                                 image_loader=image_loader, **cols)
    payload = tracks_json_payload(
        well=well, groups=groups, engine=engine,
        params={"max_distance": params.max_distance,
                "max_gap_frames": params.max_gap_frames,
                "allow_division": params.allow_division, **dict(params.extra or {})},
        fingerprints=fingerprints,
    )
    return df, payload
