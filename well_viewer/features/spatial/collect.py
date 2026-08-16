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

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

import logging
from typing import Any

import numpy as np

from pipeline.shape_features import is_shape_column
from well_viewer.data.data_loading import df_included_mask, resolve_value_series
from well_viewer.domain import spatial_compute as spc
from well_viewer.domain.metric_registry import METRICS

_logger = logging.getLogger(__name__)

CENTROID_ROW_COL = "shape_centroid_row"
CENTROID_COL_COL = "shape_centroid_col"


def _cached_df(ctx, well_label: str) -> pd.DataFrame | None:
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
    timepoint: float | None = None,
    metric_col: str = "",
    include_gate: bool = True,
) -> dict[str, Any]:
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

    metric_vals: np.ndarray | None = None
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
    metric_out: np.ndarray | None = None
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


def _sample_loaded_df(ctx) -> pd.DataFrame | None:
    """Return the first non-empty cached DataFrame across all loaded wells
    (used for picker discovery), or ``None`` if nothing is loaded."""
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    for label, df in cache.items():
        if df is not None and not df.empty:
            return df
    return None


def available_metric_columns(ctx) -> list[str]:
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

    out: list[str] = []

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


def available_timepoints(ctx) -> list[float]:
    """Unique finite ``timepoint_hours`` across every well in the cache.

    Pure ``ctx.state`` reader; empty when nothing has been loaded.
    """
    import pandas as pd
    seen: set = set()
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    for _label, df in cache.items():
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        mask = df_included_mask(df, include_gate=False)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").where(mask)
        seen.update(float(t) for t in tp.dropna().unique())
    return sorted(seen)


def collect_summary_matrix(
    ctx,
    wells: list[str],
    timepoints: list[float],
    *,
    metric_col: str = "",
    colony_method: str = spc.METHOD_DBSCAN,
    eps_mult: float = 3.0,
    min_samples: int = 4,
    include_gate: bool = True,
) -> dict[str, Any]:
    """Walk the (well × timepoint) grid, compute a :class:`FrameSummary` per
    cell, and return a payload the renderer + CSV export both consume.

    Args:
        wells: well labels to include as rows (typically the current plate
            selection, sorted plate-order by caller).
        timepoints: timepoint hours to include as columns (typically the
            union of ``available_timepoints(ctx)`` and any per-well set).
        metric_col: optional per-cell CSV column driving the ICC summary
            (empty → ICC = NaN in every frame).

    Returns:
        Dict with:
        - ``wells``: the wells actually walked (dropped: missing / empty).
        - ``timepoints``: the timepoint axis, sorted ascending.
        - ``summaries``: a 2-D object array shape ``(n_wells, n_timepoints)``
          holding :class:`FrameSummary` per cell; ``None`` where the well
          didn't have any cells at that timepoint.
        - ``metric_col``: echo of the ICC metric column (for CSV headers).
    """
    import pandas as pd
    valid_wells: list[str] = []
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    for w in wells:
        df = cache.get(w)
        if df is None or df.empty:
            continue
        valid_wells.append(w)
    tps = sorted({float(t) for t in timepoints if np.isfinite(t)})
    n_w, n_t = len(valid_wells), len(tps)
    summaries = np.empty((n_w, n_t), dtype=object)
    summaries[:] = None
    if n_w == 0 or n_t == 0:
        return {
            "wells": valid_wells, "timepoints": tps,
            "summaries": summaries, "metric_col": metric_col,
        }

    ratios = getattr(ctx.state.channel, "ratio_index", None) or {}
    for i, w in enumerate(valid_wells):
        df = cache.get(w)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        tp_col = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        finite_tp = np.isfinite(tp_col)
        base_mask = df_included_mask(df, include_gate=include_gate).to_numpy(copy=True)
        base_mask &= finite_tp
        row = pd.to_numeric(df[CENTROID_ROW_COL], errors="coerce").to_numpy()
        col = pd.to_numeric(df[CENTROID_COL_COL], errors="coerce").to_numpy()
        base_mask &= np.isfinite(row) & np.isfinite(col)
        metric_vals: np.ndarray | None = None
        if metric_col:
            try:
                metric_vals = np.asarray(
                    resolve_value_series(df, metric_col, ratios).to_numpy(),
                    dtype=float,
                )
            except Exception:
                metric_vals = None

        for j, target in enumerate(tps):
            frame_mask = base_mask & (np.abs(tp_col - target) <= 1e-6)
            idx = np.flatnonzero(frame_mask)
            if idx.size == 0:
                continue
            pts = np.column_stack([col[idx], row[idx]])
            m = metric_vals[idx] if metric_vals is not None else None
            summary = spc.summarise_frame(
                pts, metric=m,
                colony_method=colony_method,
                eps_mult=eps_mult, min_samples=min_samples,
            )
            summaries[i, j] = summary

    return {
        "wells": valid_wells,
        "timepoints": tps,
        "summaries": summaries,
        "metric_col": metric_col,
    }


def summary_matrix_to_csv_rows(payload: dict[str, Any]):
    """Flatten a ``collect_summary_matrix`` payload into per-(well,tp) rows.

    Each row is a dict keyed by ``well``, ``timepoint_hours``, and every
    :class:`FrameSummary` field name — the shape the CSV writer wants.
    """
    wells = payload.get("wells", [])
    tps = payload.get("timepoints", [])
    summaries = payload.get("summaries")
    metric_col = payload.get("metric_col") or ""
    if summaries is None or not wells or not tps:
        return []
    rows = []
    for i, w in enumerate(wells):
        for j, t in enumerate(tps):
            s = summaries[i, j]
            if s is None:
                continue
            rows.append({
                "well": w,
                "timepoint_hours": float(t),
                "n_cells": int(s.n_cells),
                "n_colonies": int(s.n_colonies),
                "nnd_median": s.nnd_median,
                "knn5_mean_median": s.knn5_mean_median,
                "local_density_median": s.local_density_median,
                "colony_size_mean": s.colony_size_mean,
                "hull_area_median": s.hull_area_median,
                "packing_density_median": s.packing_density_median,
                "icc_metric_col": metric_col,
                "icc": s.icc,
            })
    return rows
