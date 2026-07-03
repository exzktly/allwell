"""Spatial-analysis data collector — pure walker over centroid columns.

Reads ``shape_centroid_col`` / ``shape_centroid_row`` from each loaded well's
cached DataFrame (``ctx.state.dataset.cache``), honours the standard
inclusion + gating masks, and returns a plain dict of parallel arrays. No
matplotlib, no Qt, and — deliberately — no reach-through to the
``WellViewerApp`` methods: the Spatial tab operates on wells that a
plotting / image tab has already loaded, so the cache is the authoritative
per-well DataFrame source.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from pipeline.shape_features import is_shape_column
from well_viewer.data.data_loading import df_included_mask, resolve_value_series
from well_viewer.domain.metric_registry import METRICS

_logger = logging.getLogger(__name__)

CENTROID_ROW_COL = "shape_centroid_row"
CENTROID_COL_COL = "shape_centroid_col"


def _cached_df(ctx, well_label: str):
    """Return the cached per-well DataFrame or ``None`` if the well was never
    loaded. Cache-only lookup — no lazy load — so this stays a pure ``ctx.state``
    reader."""
    cache = getattr(ctx.state.dataset, "cache", None)
    if cache is None:
        return None
    return cache.get(well_label)


def collect_spatial_frame(
    ctx,
    well_label: str,
    *,
    timepoint: Optional[float] = None,
    metric_col: str = "",
    include_gate: bool = True,
) -> Dict[str, Any]:
    """Return the per-cell centroid + optional metric for a single well."""
    empty = {
        "points": np.empty((0, 2), dtype=float),
        "metric": None,
        "metric_col": metric_col,
        "timepoint": None,
        "well": well_label,
        "n_cells": 0,
    }
    df = _cached_df(ctx, well_label)
    if df is None or df.empty:
        return empty
    if CENTROID_ROW_COL not in df.columns or CENTROID_COL_COL not in df.columns:
        return empty
    if "timepoint_hours" not in df.columns:
        return empty

    import pandas as pd
    tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
    finite_tp = np.isfinite(tp)
    if not finite_tp.any():
        return empty

    if timepoint is None:
        target = float(np.min(tp[finite_tp]))
    else:
        target = float(timepoint)
    tp_match = np.abs(tp - target) <= 1e-6

    mask = df_included_mask(df, include_gate=include_gate).to_numpy(copy=True)
    mask &= finite_tp & tp_match
    if not mask.any():
        return {**empty, "timepoint": target}

    row = pd.to_numeric(df[CENTROID_ROW_COL], errors="coerce").to_numpy()
    col = pd.to_numeric(df[CENTROID_COL_COL], errors="coerce").to_numpy()
    mask &= np.isfinite(row) & np.isfinite(col)
    if not mask.any():
        return {**empty, "timepoint": target}

    metric_vals: Optional[np.ndarray] = None
    if metric_col:
        ratios = getattr(ctx.state.channel, "ratio_index", None) or {}
        try:
            metric_vals = np.asarray(
                resolve_value_series(df, metric_col, ratios).to_numpy(),
                dtype=float,
            )
        except Exception:
            metric_vals = None

    idx = np.flatnonzero(mask)
    points = np.column_stack([col[idx], row[idx]])
    metric_out: Optional[np.ndarray] = None
    if metric_vals is not None:
        metric_out = metric_vals[idx]

    return {
        "points": points,
        "metric": metric_out,
        "metric_col": metric_col,
        "timepoint": target,
        "well": well_label,
        "n_cells": int(points.shape[0]),
    }


def _sample_loaded_df(ctx):
    """Return the first non-empty cached DataFrame across all loaded wells
    (used for picker discovery), or ``None`` if nothing is loaded."""
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    for label, df in cache.items():
        if df is not None and not df.empty:
            return df
    return None


def available_metric_columns(ctx) -> List[str]:
    """List CSV columns eligible for the metric picker.

    Discovers columns from the first loaded well's DataFrame — three families
    interleaved in one flat list:
      * ``<channel>_<metric_key>`` for every fluorescence channel × metric
        registry entry actually present;
      * every ``shape_*`` morphology column present;
      * every user-defined ratio column.
    Purely ``ctx.state``-driven so no ``WellViewerApp`` reach-through is
    needed (equivalent enumeration to :func:`multivariate.collect.
    available_feature_columns` for the Spatial tab's flat-list needs).
    """
    df = _sample_loaded_df(ctx)
    if df is None:
        return []
    present = set(df.columns)

    out: List[str] = []

    channels = list(getattr(ctx.state.channel, "fluor_channels", None) or [])
    if not channels:
        inferred: set = set()
        for name in present:
            for m in METRICS:
                suffix = f"_{m.key}"
                if name.endswith(suffix):
                    inferred.add(name[: -len(suffix)])
        channels = sorted(inferred)
    for ch in channels:
        for m in METRICS:
            col = f"{ch}_{m.key}"
            if col in present and col not in out:
                out.append(col)

    for name in df.columns:
        if is_shape_column(name) and name not in out:
            out.append(name)

    ratio_index = getattr(ctx.state.channel, "ratio_index", None) or {}
    for name in ratio_index:
        if name not in out:
            out.append(name)

    return out
