"""Cell Browser data collection (Qt-free; pandas/numpy only).

Pure layer for the Cell Browser tab: enumerate the per-cell properties a dataset
exposes, and gather the ordered set of cells that satisfy a list of min/max
criteria for the selected timepoints / FOVs within the selected well(s). No Qt,
no image IO here — the worker (``worker.py``) turns the returned descriptors into
thumbnails. Mirrors ``features/scatter/collect.py``'s per-well iteration so the
two stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from well_viewer.data.data_loading import (
    detect_fluor_channels,
    detect_shape_features,
    detect_smfish_channels,
    resolve_value_series,
)
from well_viewer.data.metric_labels import split_metric_col
from well_viewer.domain.metric_registry import intensity_keys


@dataclass(frozen=True)
class Criterion:
    """One filter row: keep cells with ``min <= value(column) <= max``.

    ``column`` is anything ``resolve_value_series`` understands — a real CSV
    column (``gfp_mean_intensity``, ``shape_circularity``, ``cy5_smfish_count``)
    or a ratio key (``ratio:<name>``). ``lo`` / ``hi`` are ``None`` for "no
    bound".
    """

    column: str
    lo: Optional[float] = None
    hi: Optional[float] = None


@dataclass(frozen=True)
class CellDescriptor:
    """A single cell that passed the filter — enough to load + locate its image.

    ``tp_token`` is the raw ``timepoint`` filename token (for image lookup);
    ``timepoint_h`` is the numeric hours (for display / dedup).
    """

    well: str
    fov: str
    tp_token: str
    timepoint_h: float
    filename: str
    nucleus_id: str
    row_idx: int
    centroid_row: Optional[float]
    centroid_col: Optional[float]


def _sample_df(app):
    """The first selected (else first loaded) well's frame, for column probing."""
    sel = [lbl for lbl in app._app_state.selection.selected_wells
           if lbl in app._app_state.dataset.well_paths]
    order = sel or list(app._app_state.dataset.well_paths.keys())
    for lbl in order:
        df = app._get_rows(lbl)
        if df is not None and not df.empty:
            return df
    return None


def enumerate_properties(app) -> List[Tuple[str, str]]:
    """Ordered ``[(column_or_ratiokey, label)]`` of every measured per-cell
    property available in the current dataset, for the criteria-property picker.

    Families, in order: fluorescence intensity metrics, smFISH counts, shape
    features, ratios. Empty when no dataset is loaded.
    """
    df = _sample_df(app)
    out: List[Tuple[str, str]] = []
    if df is not None:
        for ch in detect_fluor_channels(df):
            for key in intensity_keys():
                col = f"{ch}_{key}"
                if col in df.columns:
                    parts = split_metric_col(col)
                    out.append((col, parts[2] if parts else col))
        for ch in detect_smfish_channels(df):
            col = f"{ch}_smfish_count"
            parts = split_metric_col(col)
            out.append((col, parts[2] if parts else f"{ch.upper()} (spots)"))
        from pipeline.shape_features import label_for as _shape_label_for
        for col in detect_shape_features(df):
            out.append((col, _shape_label_for(col) or col))
    # Ratios are virtual columns (no df column); offer every defined ratio.
    ratios = getattr(app._app_state.channel, "ratio_index", None) or {}
    for key, ratio in ratios.items():
        label = getattr(ratio, "name", None) or str(key)
        out.append((key, f"{label} (ratio)"))
    # Dedup preserving order.
    seen: set = set()
    deduped: List[Tuple[str, str]] = []
    for col, lbl in out:
        if col not in seen:
            seen.add(col)
            deduped.append((col, lbl))
    return deduped


def _passes_criteria(df, criteria: List[Criterion], ratios) -> np.ndarray:
    """Boolean mask over *df* of cells satisfying every criterion (AND).

    A criterion with both bounds unset is ignored. NaN values fail any set
    bound (we never include a cell we can't measure against an active filter).
    """
    mask = np.ones(len(df), dtype=bool)
    for crit in criteria:
        if crit.lo is None and crit.hi is None:
            continue
        vals = resolve_value_series(df, crit.column, ratios).to_numpy()
        if crit.lo is not None:
            mask &= np.greater_equal(vals, crit.lo, where=np.isfinite(vals),
                                     out=np.zeros_like(vals, dtype=bool))
        if crit.hi is not None:
            mask &= np.less_equal(vals, crit.hi, where=np.isfinite(vals),
                                  out=np.zeros_like(vals, dtype=bool))
    return mask


def cell_browser_source_wells(app) -> List[str]:
    """Wells the Cell Browser draws from: the explicit selection, else *every*
    loaded well.

    Mirrors the Cell Gating tab's ``_cell_gating_source_wells`` fallback so the
    gallery (and the FOV picker) populate on first open, before the user has
    picked wells on the plate — otherwise an empty selection silently yields no
    candidates."""
    paths = app._app_state.dataset.well_paths
    selected = [lbl for lbl in app._app_state.selection.selected_wells if lbl in paths]
    wells = selected or list(paths.keys())
    key = getattr(app, "_parse_rc", None) or (lambda s: s)
    return sorted(wells, key=key)


def collect_cells(
    app,
    criteria: List[Criterion],
    timepoints: Optional[List[float]] = None,
    fovs: Optional[List[str]] = None,
) -> List[CellDescriptor]:
    """Ordered list of cells (across selected wells) matching *criteria* within
    the selected *timepoints* and *fovs*.

    ``timepoints`` / ``fovs`` are a strict include-list (tri-state):

    * ``None`` → that dimension isn't filtered (e.g. a dataset with no timepoint
      column, so there's nothing to choose);
    * ``[]`` → options exist but none are selected → **no cells** match;
    * non-empty → only cells in those timepoints / FOVs.

    Criteria-only filtering per the feature spec (the app-wide Included/QC/Crop
    inclusion is intentionally NOT applied here).
    """
    ratios = getattr(app._app_state.channel, "ratio_index", None) or {}
    target = (None if timepoints is None
              else np.asarray([float(t) for t in timepoints], dtype=float))
    fov_set = None if fovs is None else {str(f) for f in fovs}

    out: List[CellDescriptor] = []
    selected = cell_browser_source_wells(app)
    for well in selected:
        df = app._get_rows(well)
        if df is None or df.empty:
            continue
        mask = _passes_criteria(df, criteria, ratios)
        if target is not None and "timepoint_hours" in df.columns:
            if target.size == 0:
                mask &= False  # options exist, none selected → no cells
            else:
                tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
                tp_match = np.zeros(len(df), dtype=bool)
                for t in target:
                    tp_match |= np.abs(tp - t) <= 1e-6
                mask &= tp_match
        if fov_set is not None and "fov" in df.columns:
            if not fov_set:
                mask &= False  # options exist, none selected → no cells
            else:
                mask &= df["fov"].astype(str).isin(fov_set).to_numpy()
        if not mask.any():
            continue
        sel = df.loc[mask]
        cols = sel.columns

        def _col(name, default=""):
            return (sel[name].astype(str).to_numpy() if name in cols
                    else np.array([default] * len(sel)))

        fnames = _col("filename")
        nids = _col("nucleus_id")
        fov_vals = _col("fov", "1")
        tp_tokens = _col("timepoint")
        tph = (pd.to_numeric(sel["timepoint_hours"], errors="coerce").to_numpy()
               if "timepoint_hours" in cols else np.full(len(sel), np.nan))
        crow = (pd.to_numeric(sel["shape_centroid_row"], errors="coerce").to_numpy()
                if "shape_centroid_row" in cols else np.full(len(sel), np.nan))
        ccol = (pd.to_numeric(sel["shape_centroid_col"], errors="coerce").to_numpy()
                if "shape_centroid_col" in cols else np.full(len(sel), np.nan))
        ridx = sel.index.to_numpy()
        for i in range(len(sel)):
            out.append(CellDescriptor(
                well=well, fov=str(fov_vals[i]), tp_token=str(tp_tokens[i]),
                timepoint_h=float(tph[i]) if np.isfinite(tph[i]) else float("nan"),
                filename=str(fnames[i]), nucleus_id=str(nids[i]),
                row_idx=int(ridx[i]),
                centroid_row=float(crow[i]) if np.isfinite(crow[i]) else None,
                centroid_col=float(ccol[i]) if np.isfinite(ccol[i]) else None,
            ))
    return out
