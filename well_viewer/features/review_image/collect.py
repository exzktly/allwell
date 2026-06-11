"""Review-image data layer (no Qt, no matplotlib).

The pure data-derivation / query half of the Review Image feature: build the
per-cell include + threshold maps, project Segmentation-tab overrides onto the
row cache, resolve image refs + flat-field fields, and the row-key / timepoint /
row-loading helpers. Split out of ``review_image/controller.py`` (T4) so the
controller keeps the Qt event handlers + table/canvas wiring while this module
owns the data. ``review_image/controller`` re-exports these names, and the
matplotlib drawing stays in ``review_image/renderer``.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import numpy as _np
import pandas as pd

from well_viewer.data.data_loading import df_included_mask, resolve_value_series
from well_viewer.data.image_resolver import resolve_ref_by_fov_tp as _resolve_ref_by_fov_tp
from well_viewer.core.ui_helpers import safe_int_or_none as _safe_int_or_none
from well_viewer.core.safe import swallowed

_logger = logging.getLogger(__name__)

# Keep the on-the-fly preview field cheap: the illumination envelope is smooth,
# so a handful of frames is plenty for the basic/empty-well median estimate.
_FLATFIELD_PREVIEW_MAX_SAMPLES = 8


def review_row_keys_series(app, df: pd.DataFrame) -> Optional[pd.Series]:
    """Vectorized counterpart of ``_review_row_keys`` for a DataFrame.

    Returns a ``Series`` of ``(fov, tp, nid)`` tuples aligned with
    ``df.index``, or ``None`` if no usable identifier columns are present.

    Built on :func:`image_identity.cell_keys_for_dataframe` so this side
    of the lookup uses the same well/fov/tp/nid normalisation chain the
    smFISH writer and ``cell_overrides`` use — no more drift between the
    review-image overrides side (used to use ``:g`` float format) and the
    smFISH writer side (used to strip only)."""
    if df is None or df.empty:
        return None
    from well_viewer.data.image_identity import cell_keys_for_dataframe
    keys = cell_keys_for_dataframe(df)
    if keys.empty:
        return None
    # Strip the well token — historical callers want a 3-tuple
    # (fov, tp, nid) keyed off the active well selection separately.
    return pd.Series([(k.fov, k.tp, k.nid) for k in keys], index=df.index)


def review_resolve_image_refs(app, *, fov_raw: str, tp_raw: str):
    """Resolve (fluor_ref, mask_ref).

    Honors ``image_source``: ``"corrected"`` (default) prefers the top-hat
    output and falls back to raw; ``"raw"`` and ``"flatfield"`` both resolve the
    raw frame (raw is the flat-field input — the renderer applies the flat-field
    to it) and fall back to top-hat. Falling back rather than failing keeps
    Review Image usable on datasets that only ship one of the two image kinds
    for a given FOV/timepoint.
    """
    raw_first = app._app_state.review_image.image_source in ("raw", "flatfield")
    tophat_map = getattr(app, "_preview_tophat_fluor", {})
    raw_map = app._app_state.preview.fluor
    if raw_first:
        primary, fallback = raw_map, tophat_map
    else:
        primary, fallback = tophat_map, raw_map
    fluor_ref = _resolve_ref_by_fov_tp(
        primary, fov_raw=fov_raw, tp_raw=tp_raw,
        norm_timepoint=app._norm_timepoint,
    )
    if fluor_ref is None:
        fluor_ref = _resolve_ref_by_fov_tp(
            fallback, fov_raw=fov_raw, tp_raw=tp_raw,
            norm_timepoint=app._norm_timepoint,
        )
    mask_ref = _resolve_ref_by_fov_tp(
        app._app_state.preview.mask, fov_raw=fov_raw, tp_raw=tp_raw,
        norm_timepoint=app._norm_timepoint,
    )
    return fluor_ref, mask_ref


def _flatfield_qc(app, key: str, default: str = "") -> str:
    info = app._app_state.dataset.pipeline_info or {}
    qc = info.get("qc_options") or {}
    return str(qc.get(key, default) or default).strip()


def _split_background_wells(value) -> "list[str]":
    """Parse a comma-separated ``background_well`` value into a deduped well list
    (case-insensitive). A single ``"H12"`` → ``["H12"]`` (back-compat)."""
    seen, out = set(), []
    for tok in str(value or "").replace(";", ",").split(","):
        w = tok.strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def _load_arrays_from_refs(refs, *, limit: int, tp=None):
    """Decode up to *limit* arrays from a ``(fov,tp)->ImgRef`` map.

    When *tp* is given, only frames whose timepoint normalizes to the same key
    are decoded (per-timepoint empty-well field); otherwise all frames pool."""
    from well_viewer.data.image_discovery import open_imgref_as_array
    from well_viewer.data.image_identity import normalize_tp
    want = normalize_tp(tp) if tp is not None else None
    out = []
    for k, ref in list((refs or {}).items()):
        if want is not None:
            ref_tp = k[1] if isinstance(k, tuple) and len(k) > 1 else k
            if normalize_tp(ref_tp) != want:
                continue
        arr = open_imgref_as_array(ref, greyscale=True)
        if arr is not None:
            out.append(_np.asarray(arr, dtype=_np.float32))
        if len(out) >= limit:
            break
    return out


def flatfield_field_for(app, channel: str, fluor_arr, tp=None):
    """Return the mean-1 flat-field field for the current view, or None (identity).

    Recomputes the flat-field METHOD the pipeline used (read from
    ``pipeline_info['qc_options']['flatfield']``) so the Segmentation tab can
    show the flat-field stage in isolation, before any background subtraction.
    Cached per ``(channel, mode, tp)`` in ``ReviewImageState.flatfield_field_cache``.

    Fidelity: exact for ``none`` / ``gaussian`` / ``empty_well`` (same estimator
    and inputs the pipeline used); ``basic`` is approximated from the *current
    well's* FOVs (the pipeline samples the whole plate). ``empty_well`` pools all
    comma-separated background wells and builds the field from the frames at the
    *displayed* timepoint (background drifts over time), falling back to the
    all-timepoint pooled estimate when that timepoint is absent. The estimate is
    capped at a few frames and cached so the UI thread isn't blocked on repeats.
    """
    import flatfield_core as _ffc

    mode = _flatfield_qc(app, "flatfield", "none").lower()
    if mode in ("", "none"):
        return None
    if mode == "gaussian":
        # Self-referential (one image) — cheap, recomputed per frame.
        return _ffc.estimate_gaussian_flatfield(fluor_arr)

    chan = str(channel or "").lower()
    from well_viewer.data.image_identity import normalize_tp
    tp_key = normalize_tp(tp) if (mode == "empty_well" and tp is not None) else ""
    cache = app._app_state.review_image.flatfield_field_cache
    key = (chan, mode, tp_key)
    if key in cache:
        return cache[key]

    field = None
    try:
        if mode == "basic":
            if hasattr(app, "_set_status"):
                app._set_status("Computing flat-field (basic) for preview…")
            arrs = _load_arrays_from_refs(
                app._app_state.preview.fluor, limit=_FLATFIELD_PREVIEW_MAX_SAMPLES)
            field = _ffc.estimate_basic_flatfield(arrs) if arrs else None
        elif mode == "empty_well":
            wells = _split_background_wells(_flatfield_qc(app, "background_well", ""))
            if wells:
                if hasattr(app, "_set_status"):
                    app._set_status("Computing flat-field (empty well) for preview…")
                from well_viewer.data.image_discovery import find_well_images_and_masks
                ds = app._app_state.dataset
                refs_by_well = []
                for w in wells:
                    fluor, _ov, _mk, _th = find_well_images_and_masks(
                        ds.data_dir, w, fluor_token=channel,
                        in_dir=ds.in_dir, _fov_tp_extractor=ds.fov_tp_extractor,
                        _pipeline_info=ds.pipeline_info,
                    )
                    if fluor:
                        refs_by_well.append(fluor)
                # Prefer frames at the displayed timepoint; pool across wells.
                arrs = []
                if tp is not None:
                    for refs in refs_by_well:
                        arrs.extend(_load_arrays_from_refs(
                            refs, limit=_FLATFIELD_PREVIEW_MAX_SAMPLES, tp=tp))
                if not arrs:
                    # Global pooled-median fallback (timepoint absent / no tp).
                    for refs in refs_by_well:
                        arrs.extend(_load_arrays_from_refs(
                            refs, limit=_FLATFIELD_PREVIEW_MAX_SAMPLES))
                field = _ffc.estimate_basic_flatfield(arrs) if arrs else None
    except Exception:  # noqa: BLE001 — a failed preview field falls back to raw
        _logger.exception("flat-field preview recompute failed (mode=%s)", mode)
        field = None

    cache[key] = field
    return field


def review_build_include_map(
    app, mask_arr, well: str, fov: str, tp: str,
) -> Dict[int, bool]:
    """Build {nid: is_included} for all labels in ``mask_arr``."""
    center = _np.asarray(mask_arr)
    include_by_nid: Dict[int, bool] = {
        int(nid): True for nid in _np.unique(center) if int(nid) > 0
    }
    df = app._get_rows(well)
    if df is not None and not df.empty:
        keys = review_row_keys_series(app,df)
        if keys is not None:
            fov_eq = keys.map(lambda k: k[0] == fov and k[1] == tp and k[2] != "")
            sub = df.loc[fov_eq.fillna(False).to_numpy()]
            if not sub.empty:
                sub_keys = keys.loc[sub.index]
                nids = sub_keys.map(lambda k: k[2]).map(_safe_int_or_none)
                incl = sub["Included"].map(lambda v: int(float(str(v).strip() or "1")) if str(v).strip() not in ("",) else 1)
                # QC layer (QC_Pass) composes with manual/gating Included: a
                # cell shows excluded when it fails QC *or* is manually/gated
                # out (effective = manual-keep AND passes-QC). Absent column →
                # all-pass (older data / QC off).
                if "QC_Pass" in sub.columns:
                    qc = sub["QC_Pass"].map(lambda v: int(float(str(v).strip() or "1")) if str(v).strip() not in ("",) else 1)
                else:
                    qc = pd.Series(1, index=sub.index)
                # Crop layer (Crop_Pass) composes the same way: a cell whose
                # centroid is outside the crop region shows excluded. Absent
                # column → all-pass (no crop / older data).
                if "Crop_Pass" in sub.columns:
                    crop = sub["Crop_Pass"].map(lambda v: int(float(str(v).strip() or "1")) if str(v).strip() not in ("",) else 1)
                else:
                    crop = pd.Series(1, index=sub.index)
                overrides = app._review_included_overrides
                for nid, base_incl, qc_pass, crop_pass in zip(nids, incl, qc, crop):
                    if nid is None:
                        continue
                    ovr = overrides.get((well, fov, tp, str(nid)))
                    if ovr is not None:
                        inc = (str(ovr).strip() != "0")
                    elif nid in include_by_nid:
                        inc = bool(base_incl)
                    else:
                        continue
                    include_by_nid[nid] = inc and bool(qc_pass) and bool(crop_pass)
    # Apply overrides for cells present in the mask but without a CSV row
    # (e.g. when no pipeline results are loaded yet).
    for (ovr_well, ovr_fov, ovr_tp, ovr_nid), val in app._review_included_overrides.items():
        if ovr_well != well or ovr_fov != fov or ovr_tp != tp:
            continue
        nid = _safe_int_or_none(ovr_nid)
        if nid is None:
            continue
        if nid in include_by_nid:
            include_by_nid[nid] = (val.strip() != "0")
    return include_by_nid


def _binary_gate_columns(app):
    """Return ``(val_col, threshold)`` for the Segmentation binary mask.

    Keyed to the **displayed image channel** (``active_image_channel``) — the
    channel the Segmentation tab is actually showing — so "Binary (≥ ON
    threshold)" binarises the channel you're viewing. The global plotting
    channel (``active_val_col`` / the active-channel threshold) can differ and
    would otherwise pull the wrong channel. Falls back to the plotting column
    when no image channel is set (e.g. the nuc+seg overlay).
    """
    img_chan = str(app._app_state.channel.active_image_channel or "").strip()
    if img_chan:
        return (f"{img_chan}_{app._app_state.channel.active_metric}",
                app._get_thresh_frac_on(img_chan))
    return (app._app_state.channel.active_val_col, app._get_thresh_frac_on())


def review_build_threshold_map(
    app, mask_arr, well: str, fov: str, tp: str,
) -> Dict[int, bool]:
    """Build {nid: above_threshold} for cells in the displayed FOV/timepoint.

    Mirrors the bar plot's gating logic: a cell is "above threshold" only when it
    passes (a) cell-area gating, (b) every fluorescence gate, and (c)
    ``row[val_col] > thresh_frac_on``. The value column + ON threshold are taken
    from the **displayed image channel** (``active_image_channel``) — the channel
    the Segmentation tab is actually showing — not the global plotting channel
    (``active_val_col``), which can differ and would otherwise binarise the
    wrong channel.
    """
    center = _np.asarray(mask_arr)
    fluor_gates = app._get_all_fluor_gates()
    # Gate against the channel being viewed on the Segmentation tab, not the
    # global plotting channel — see _binary_gate_columns.
    val_col, threshold = _binary_gate_columns(app)
    ratios = app._app_state.channel.ratio_index
    above_by_nid: Dict[int, bool] = {
        int(nid): False for nid in _np.unique(center) if int(nid) > 0
    }
    df = app._get_rows(well)
    if df is None or df.empty:
        return above_by_nid
    keys = review_row_keys_series(app,df)
    if keys is None:
        return above_by_nid
    sel = keys.map(lambda k: k[0] == fov and k[1] == tp and k[2] != "").fillna(False).to_numpy()
    sub = df.loc[sel]
    if sub.empty:
        return above_by_nid

    mask = df_included_mask(sub).to_numpy(copy=True)
    n_incl_qc = int(mask.sum())
    if "area_px" in sub.columns:
        area = pd.to_numeric(sub["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(area)
    n_after_area = int(mask.sum())
    missing_fluor_col = None
    for chan, gate in fluor_gates.items():
        col = f"{chan}_mean_intensity"
        if col not in sub.columns:
            missing_fluor_col = col
            mask[:] = False
            break
        v = pd.to_numeric(sub[col], errors="coerce").to_numpy()
        mask &= np.isfinite(v) & (v > gate)
    n_after_fluor = int(mask.sum())
    val = resolve_value_series(sub, val_col, ratios).to_numpy()
    mask &= np.isfinite(val) & (val > threshold)
    n_above = int(mask.sum())

    sub_keys = keys.loc[sub.index]
    for k, ok in zip(sub_keys, mask):
        nid = _safe_int_or_none(k[2])
        if nid is not None and nid in above_by_nid:
            above_by_nid[nid] = bool(ok)

    # Diagnostic: the gating math is unit-tested correct, so when the binary
    # mask highlights the "wrong" cells the cause is in the inputs — the
    # resolved value column, the threshold, the QC verdict, or a missing gate
    # column. One concise line per build surfaces all of them so the cause is
    # visible in the log without a debugger.
    try:
        finite_vals = val[_np.isfinite(val)]
        vmin = float(finite_vals.min()) if finite_vals.size else float("nan")
        vmax = float(finite_vals.max()) if finite_vals.size else float("nan")
        _logger.info(
            "[binary-mask] well=%s fov=%s tp=%s | val_col=%r thresh=%.4g | "
            "mask_cells=%d matched_rows=%d incl+qc=%d after_area=%d "
            "after_fluor=%d above=%d | val_range=[%.4g,%.4g] qc_col=%s "
            "fluor_gates=%s%s",
            well, fov, tp, val_col, threshold,
            len(above_by_nid), len(sub), n_incl_qc, n_after_area,
            n_after_fluor, n_above, vmin, vmax,
            ("QC_Pass" in sub.columns),
            {k: round(float(g), 4) for k, g in fluor_gates.items()},
            f" MISSING_FLUOR_COL={missing_fluor_col}" if missing_fluor_col else "",
        )
    except Exception:  # diagnostics must never break rendering
        _logger.debug("[binary-mask] diagnostic log failed", exc_info=True)
    return above_by_nid


def apply_review_overrides_to_cache(app, label: Optional[str] = None) -> None:
    """Project overrides onto cached row['Included'] so non-Review tabs see them.

    The override dict is the source of truth; this projects values onto the
    cached rows so every controller using row_is_included(row) automatically
    respects user toggles. Called after gating recomputes (which rewrite
    row['Included'] from thresholds), after patch-file load, and on toggle.
    """
    if not app._review_included_overrides:
        return
    by_label: Dict[str, Dict[Tuple[str, str, str], str]] = {}
    for (lbl, fov, tp, nid), val in app._review_included_overrides.items():
        if label is not None and lbl != label:
            continue
        by_label.setdefault(lbl, {})[(fov, tp, nid)] = val
    for lbl, lookup in by_label.items():
        if lbl not in app._app_state.dataset.well_paths:
            continue
        try:
            df = app._get_rows(lbl)
        except Exception:
            swallowed()  # silent except
            continue
        if df is None or df.empty:
            continue
        keys = review_row_keys_series(app,df)
        if keys is None:
            continue
        override_map = pd.Series(lookup)
        override_for_row = keys.map(override_map)
        mask = override_for_row.notna()
        if not mask.any():
            continue
        def _coerce(val: object) -> int:
            try:
                return int(float(str(val).strip() or "1"))
            except (TypeError, ValueError):
                return 1 if str(val).strip() == "1" else 0
        df.loc[mask, "Included"] = override_for_row[mask].map(_coerce).astype(int)


def review_row_keys(app, row: dict) -> Tuple[str, str, str]:
    def _norm(v: object) -> str:
        s = str(v or "").strip()
        if not s:
            return ""
        try:
            return f"{float(s):g}"
        except Exception:
            return s
    def _pick(*names: str) -> object:
        for name in names:
            if name in row:
                return row.get(name, "")
        lowered = {str(k).lower(): v for k, v in row.items()}
        for name in names:
            key = name.lower()
            if key in lowered:
                return lowered[key]
        return ""
    return (
        _norm(_pick("fov", "FOV")),
        app._norm_timepoint(_pick("timepoint", "tp", "time", "time_h", "timepoint_hours")),
        _norm(_pick("nucleus_id", "nucleus id", "nucleusId", "nucleusID")),
    )


def review_collect_timepoints(app, fov: str) -> list:
    """Sorted list of timepoints available for ``fov`` (fluor + pipeline)."""
    _norm = app._review_norm_fov
    tp_values = sorted(
        {
            app._norm_timepoint(tp)
            for (f, tp) in app._app_state.preview.fluor.keys()
            if _norm(f) == fov and app._norm_timepoint(tp)
        },
        key=app._review_tp_sort_key,
    )
    pipeline_tp_values = sorted(
        {
            app._norm_timepoint(tp)
            for tp in (app._app_state.dataset.pipeline_info or {}).get("available_timepoints", [])
            if app._norm_timepoint(tp)
        },
        key=app._review_tp_sort_key,
    )
    if pipeline_tp_values:
        tp_values = sorted(set(tp_values) | set(pipeline_tp_values), key=app._review_tp_sort_key)
    return tp_values


def review_load_rows(app, label: str) -> List[dict]:
    """Materialize the cached DataFrame for ``label`` as a list of row dicts.

    Used by the Review CSV table widget and the Review Image overlay
    builders. Mutations to returned dicts are local — write back to the
    canonical store via ``_apply_review_overrides_to_cache`` (which
    updates the cached DataFrame's ``Included`` column directly).
    """
    try:
        df = app._get_rows(label)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def review_effective_included(app, label: str, row: dict) -> str:
    """Effective Included value for a cached row.

    User overrides set via the Review Image tab take precedence over the
    gating-computed ``Included`` field stored on the cached row. Overrides
    are kept in a separate dict so they survive subsequent gating recomputes
    (which rewrite the canonical row['Included']).
    """
    fov, tp, nid = app._review_row_keys(row)
    val = None
    if fov and tp and nid:
        ovr = app._review_included_overrides.get((label, fov, tp, nid))
        if ovr is not None:
            val = ovr
    if val is None:
        val = str(row.get("Included", "1"))
    # Compose the QC and crop layers: a QC-failed cell (QC_Pass == 0) or a cell
    # cropped out (Crop_Pass == 0) is excluded regardless of manual curation —
    # effective = manual-keep AND passes-QC AND inside-crop.
    for col in ("QC_Pass", "Crop_Pass"):
        flag = row.get(col, 1)
        try:
            if int(float(str(flag).strip() or "1")) == 0:
                return "0"
        except (TypeError, ValueError):
            pass
    return val


def review_norm_fov(v: object) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        return f"{float(s):g}"
    except Exception:
        return s
