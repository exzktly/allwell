"""Spatial-analysis data collector — pure walker over centroid columns.

Reads ``shape_centroid_col`` / ``shape_centroid_row`` from each well's rows,
through ``data_loading.get_rows`` — one well at a time, since the row cache is
bounded and a plate need not fit in it — honours the standard inclusion +
gating masks, and returns a plain dict of parallel arrays. No matplotlib, no
Qt, and no reach-through to the ``ReviewApp`` methods.
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
from review.data.data_loading import df_included_mask, get_rows, resolve_value_series, resolved_timepoints
from review.data.plate_scan import every_well
from review.domain import spatial_compute as spc
from review.domain.metric_registry import METRICS
from domain.tp_match import tp_close_mask

_logger = logging.getLogger(__name__)

CENTROID_ROW_COL = "shape_centroid_row"
CENTROID_COL_COL = "shape_centroid_col"
#: A nanowell run's per-cell position in the well frame (tracker I2): one frame for every tile of the well, in µm.
WELL_X_COL = "well_x_um"
WELL_Y_COL = "well_y_um"


def position_columns(df) -> tuple[str, str, str] | None:
    """``(x column, y column, unit)`` for the cells' positions, or ``None`` when the frame has none.

    A nanowell run states each cell's position in the well frame (``well_x_um``, ``well_y_um``), shared by every
    tile of the well, so a well's cells from several tiles land where they are; that frame wins. Otherwise the
    segmentation's centroid in its field's pixels, which places cells correctly only within one field.
    """
    if WELL_X_COL in df.columns and WELL_Y_COL in df.columns:
        return WELL_X_COL, WELL_Y_COL, "µm"
    if CENTROID_ROW_COL in df.columns and CENTROID_COL_COL in df.columns:
        return CENTROID_COL_COL, CENTROID_ROW_COL, "px"
    return None


def _hours(df) -> np.ndarray:
    """Each row's timepoint in hours: ``timepoint_hours``, else the ``timepoint`` label read as hours (a nanowell
    run's cell table has only the label), through the loader's one resolver."""
    return np.asarray(resolved_timepoints(df), dtype=float)


def _cached_df(ctx, well_label: str) -> pd.DataFrame | None:
    """The well's rows, read through ``get_rows``; ``None`` for a well the
    dataset does not have.

    Not a cache-only lookup any more. The row cache is bounded
    (``review/data/row_cache.py``), so "not in the cache" now also means
    "evicted", and a cache-only read drew an evicted well as an empty frame."""
    if well_label not in ctx.state.dataset.well_paths:
        return None
    return get_rows(ctx, well_label)


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
    columns = position_columns(df)
    if columns is None:
        return empty
    x_col, y_col, unit = columns
    empty["unit"] = unit

    import pandas as pd
    tp = _hours(df)
    finite_tp = np.isfinite(tp)
    if not finite_tp.any():
        return empty

    if timepoint is None:
        target = float(np.min(tp[finite_tp]))
    else:
        target = float(timepoint)
    tp_match = tp_close_mask(tp, target)

    mask = df_included_mask(df, include_gate=include_gate).to_numpy(copy=True)
    mask &= finite_tp & tp_match
    if not mask.any():
        return {**empty, "timepoint": target}

    col = pd.to_numeric(df[x_col], errors="coerce").to_numpy()
    row = pd.to_numeric(df[y_col], errors="coerce").to_numpy()
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
        "unit": unit,
    }


def _sample_loaded_df(ctx) -> pd.DataFrame | None:
    """A non-empty well's frame for picker discovery, or ``None`` with no data.

    A held frame when there is one — any well answers which columns exist —
    else the first well read from disk: with the row cache bounded, an empty
    cache no longer means an empty dataset."""
    for _label, df in ctx.state.dataset.cache.items():
        if df is not None and not df.empty:
            return df
    for label in list(ctx.state.dataset.well_paths):
        df = get_rows(ctx, label)
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
    Purely ``ctx.state``-driven so no ``ReviewApp`` reach-through is
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
    """Unique finite included timepoints, in hours, across every well.

    Every well, not every *held* well: the row cache is bounded, so each well is
    read narrowly — the timepoint columns and the verdicts' inputs, which a
    narrow read always carries — and its timepoints remembered (``plate_scan``)
    until its rows or verdicts change. Empty when nothing has been loaded.
    Not :func:`data_loading.get_all_timepoints`' scan: this one falls back to
    the ``timepoint`` label where ``timepoint_hours`` is missing.
    """
    seen: set = set()
    for tps in every_well(ctx, "spatial_timepoints", None, _included_hours,
                          columns=("timepoint_hours", "timepoint")).values():
        seen.update(tps)
    return sorted(seen)


def _included_hours(df) -> frozenset:
    if df is None or df.empty:
        return frozenset()
    mask = df_included_mask(df, include_gate=False).to_numpy()
    tp = _hours(df)
    return frozenset(float(t) for t in np.unique(tp[mask & np.isfinite(tp)]))


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
    # Each well is read once, through ``get_rows``, and its row of the matrix
    # filled before the next is read — so a plate larger than the row-cache
    # budget is walked without holding it whole. A missing or empty well is
    # dropped, as before.
    tps = sorted({float(t) for t in timepoints if np.isfinite(t)})
    ratios = getattr(ctx.state.channel, "ratio_index", None) or {}
    units: set = set()
    valid_wells: list[str] = []
    rows: list[list] = []
    for w in wells:
        if w not in ctx.state.dataset.well_paths:
            continue
        df = get_rows(ctx, w)
        if df is None or df.empty:
            continue
        valid_wells.append(w)
        rows.append(_summary_row(df, tps, metric_col, ratios, units, include_gate=include_gate,
                                 colony_method=colony_method, eps_mult=eps_mult,
                                 min_samples=min_samples))
    summaries = np.empty((len(valid_wells), len(tps)), dtype=object)
    summaries[:] = None
    for i, row in enumerate(rows):
        for j, summary in enumerate(row):
            summaries[i, j] = summary
    if not valid_wells or not tps:
        return {
            "wells": valid_wells, "timepoints": tps,
            "summaries": summaries, "metric_col": metric_col,
        }

    return {
        "wells": valid_wells,
        "timepoints": tps,
        "summaries": summaries,
        "metric_col": metric_col,
        # One unit for the table; wells in two frames (a merged plate) cannot share distances, and say so.
        "unit": next(iter(units)) if len(units) == 1 else ("mixed: µm and px" if units else "px"),
    }


def _summary_row(df, tps, metric_col, ratios, units: set, *, include_gate: bool,
                 colony_method: str, eps_mult: float, min_samples: int) -> list:
    """One well's :class:`FrameSummary` per timepoint (``None`` where it has no cells)."""
    import pandas as pd
    out: list = [None] * len(tps)
    columns = position_columns(df)
    if columns is None:
        return out
    units.add(columns[2])
    tp_col = _hours(df)
    finite_tp = np.isfinite(tp_col)
    base_mask = df_included_mask(df, include_gate=include_gate).to_numpy(copy=True)
    base_mask &= finite_tp
    col = pd.to_numeric(df[columns[0]], errors="coerce").to_numpy()
    row = pd.to_numeric(df[columns[1]], errors="coerce").to_numpy()
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
        frame_mask = base_mask & tp_close_mask(tp_col, target)
        idx = np.flatnonzero(frame_mask)
        if idx.size == 0:
            continue
        pts = np.column_stack([col[idx], row[idx]])
        m = metric_vals[idx] if metric_vals is not None else None
        out[j] = spc.summarise_frame(
            pts, metric=m,
            colony_method=colony_method,
            eps_mult=eps_mult, min_samples=min_samples,
        )
    return out


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
