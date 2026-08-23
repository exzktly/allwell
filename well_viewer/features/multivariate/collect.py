"""Multivariate feature-matrix collection (no compute, no rendering).

The pure data-gathering half of the Multivariate feature: walk the selected
wells / replicate sets, apply the standard inclusion + gating masks, resolve each
selected feature column (ratio-aware), and stack the per-cell values into a
single ``n_cells x n_features`` matrix with parallel group / well / timepoint
label arrays. No matplotlib / Qt beyond a colour lookup — the compute
(``domain.multivariate_compute``) and the renderer both consume plain arrays.

Mirrors the well-walk + gating of ``features/scatter/collect.py`` (same
``df_included_mask`` / ``resolve_value_series`` / replicate-set-vs-selection
logic) but generalised from two axis columns to an arbitrary feature set.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from well_viewer.data.data_loading import df_included_mask, resolve_value_series, get_rows
from well_viewer.domain.metric_registry import METRIC_BY_KEY
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.domain.selections_model import color_for_replicate_set, color_for_well, active_replicate_sets
from well_viewer.domain.tp_match import tp_close_mask

_logger = logging.getLogger(__name__)


def column_display_label(ctx, col: str) -> str:
    """Human-readable label for a feature column (drives picker + axis text).

    Handles the three column families: shape/morphology (via the shape-feature
    catalog's ``label_for``), ratios (via the live ratio index), and the
    ``<channel>_<metric>`` intensity/count columns (channel upper-cased + the
    metric registry's label).
    """
    from pipeline.shape_features import is_shape_column, label_for as shape_label
    if is_shape_column(col):
        return shape_label(col) or col
    ratio_index = getattr(ctx.state.channel, "ratio_index", None) or {}
    if col in ratio_index:
        rm = ratio_index[col]
        return getattr(rm, "label", None) or getattr(rm, "name", None) or col
    # <channel>_<metric_key> — split on the longest known metric suffix.
    for key, mdef in METRIC_BY_KEY.items():
        suffix = f"_{key}"
        if col.endswith(suffix):
            channel = col[: -len(suffix)]
            return f"{channel.upper()} {mdef.label}"
    return col


def collect_feature_matrix(
    ctx,
    *,
    feature_cols: list[str],
    timepoints=None,
    include_gate: bool = True,
) -> dict[str, Any]:
    """Assemble the per-cell feature matrix for the current well selection.

    Args:
        app: WellViewerApp (state + per-well DataFrame access).
        feature_cols: CSV columns forming the matrix, in display order.
        timepoints: optional iterable of target timepoints (hours); ``None`` /
            empty means every timepoint. A cell is kept if its
            ``timepoint_hours`` matches any target within the shared relative
            tolerance (``domain.tp_match``).
        include_gate: honour the scatter-gate (``Gate_Pass``) layer too — the
            multivariate matrix analyses the fully-included population by default.

    Returns a dict:
        ``X`` (n_cells x n_features float), ``feature_cols`` / ``feature_labels``,
        ``group`` / ``well`` (object arrays), ``timepoint`` (float array),
        ``group_colors`` (name→colour), ``n_cells``, ``n_dropped_nan``.
        Empty selection → ``X`` shape ``(0, n_features)``.
    """
    feature_cols = list(feature_cols or [])
    n_feat = len(feature_cols)
    ratios = getattr(ctx.state.channel, "ratio_index", None) or {}

    target_arr = None
    if timepoints:
        target_arr = np.asarray([float(t) for t in timepoints], dtype=float)

    rows_X: list[np.ndarray] = []
    grp_labels: list[str] = []
    well_labels: list[str] = []
    tp_values: list[float] = []
    group_colors: dict[str, Any] = {}
    # Per-well colours keyed by well label (absolute plate-position rank), built
    # in BOTH modes so the renderer's "colour by: well" option works even when
    # replicate-set groups are active (group_colors is keyed by group name there).
    well_colors: dict[str, Any] = {}
    dropped = 0

    def _collect_well(well_label: str, group_name: str):
        nonlocal dropped
        df = get_rows(ctx, well_label)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            return
        mask = df_included_mask(df, include_gate=include_gate).to_numpy(copy=True)
        import pandas as pd
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        finite_tp = np.isfinite(tp)
        if target_arr is not None:
            if target_arr.size == 0:
                return
            tp_match = np.zeros_like(tp, dtype=bool)
            for t in target_arr:
                tp_match |= tp_close_mask(tp, t)
            mask &= finite_tp & tp_match
        else:
            mask &= finite_tp
        if not mask.any():
            return
        # Cells passing gate + timepoint, before requiring finite features — the
        # difference after feature-finiteness is what got dropped for NaN.
        base_count = int(np.count_nonzero(mask))
        cols_vals: list[np.ndarray] = []
        for col in feature_cols:
            vals = np.asarray(resolve_value_series(df, col, ratios).to_numpy(), dtype=float)
            cols_vals.append(vals)
            mask &= np.isfinite(vals)
        sel = np.flatnonzero(mask)
        dropped += base_count - sel.size
        if sel.size == 0:
            return
        block = np.column_stack([v[sel] for v in cols_vals]) if n_feat else \
            np.empty((sel.size, 0), dtype=float)
        rows_X.append(block)
        grp_labels.extend([group_name] * sel.size)
        well_labels.extend([well_label] * sel.size)
        tp_values.extend(tp[sel].tolist())

    active_rsets = active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)
    if active_rsets:
        for rset in active_rsets:
            group_name = rset.name
            group_colors[group_name] = color_for_replicate_set(
                rset, ctx.state.selection.selections)
            for well_label in rset.wells:
                if well_label in ctx.state.dataset.well_paths:
                    well_colors[well_label] = color_for_well(well_label)
                    _collect_well(well_label, group_name)
    else:
        selected = sorted(
            (lbl for lbl in ctx.state.selection.selected_wells
             if lbl in ctx.state.dataset.well_paths),
            key=lambda lbl: parse_rc(lbl),
        )
        for well_label in selected:
            group_colors[well_label] = color_for_well(well_label)
            well_colors[well_label] = color_for_well(well_label)
            _collect_well(well_label, well_label)

    if rows_X:
        X = np.vstack(rows_X)
    else:
        X = np.empty((0, n_feat), dtype=float)

    return {
        "X": X,
        "feature_cols": feature_cols,
        "feature_labels": [column_display_label(ctx, c) for c in feature_cols],
        "group": np.asarray(grp_labels, dtype=object),
        "well": np.asarray(well_labels, dtype=object),
        "timepoint": np.asarray(tp_values, dtype=float),
        "group_colors": group_colors,
        "well_colors": well_colors,
        "n_cells": int(X.shape[0]),
        "n_dropped_nan": int(dropped),
    }


def available_feature_columns(ctx, well_label: str | None = None) -> dict[str, list[str]]:
    """Discover the feature columns present in the loaded dataset, by family.

    Dynamically derived per-dataset (never hardcoded): intensity/count columns
    from the metric registry crossed with the detected fluorescence channels,
    the ``shape_*`` morphology columns actually emitted for this run
    (``detect_shape_features``), and any active ratio columns. Returns
    ``{"Intensity": [...], "Shape": [...], "Ratios": [...]}`` with display order
    preserved; empty families are omitted by the caller.
    """
    from well_viewer.data.data_loading import detect_shape_features
    from well_viewer.domain.metric_registry import METRICS

    families: dict[str, list[str]] = {"Intensity": [], "Shape": [], "Ratios": []}

    # Sample a loaded well's DataFrame to read the actual columns present.
    df = None
    label = well_label
    if label is None:
        for lbl in ctx.state.dataset.well_paths:
            candidate = get_rows(ctx, lbl)
            if candidate is not None and not candidate.empty:
                df = candidate
                break
    else:
        df = get_rows(ctx, label)
    if df is None or df.empty:
        return {}

    present = set(df.columns)
    channels = list(getattr(ctx.state.channel, "fluor_channels", None) or [])
    if not channels:
        # Fall back to inferring channel tokens from the intensity columns present.
        inferred = set()
        for col in present:
            for m in METRICS:
                suffix = f"_{m.key}"
                if col.endswith(suffix):
                    inferred.add(col[: -len(suffix)])
        channels = sorted(inferred)
    for ch in channels:
        for m in METRICS:
            col = f"{ch}_{m.key}"
            if col in present:
                families["Intensity"].append(col)

    families["Shape"] = list(detect_shape_features(df))

    ratio_index = getattr(ctx.state.channel, "ratio_index", None) or {}
    families["Ratios"] = list(ratio_index.keys())

    return {k: v for k, v in families.items() if v}
