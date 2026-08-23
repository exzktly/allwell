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

import numpy as np
import numpy as _np
import pandas as pd

from well_viewer.data.data_loading import df_included_mask, get_rows, resolve_value_series
from well_viewer.data.image_resolver import normalize_numeric_token
from well_viewer.data.image_resolver import resolve_ref_by_fov_tp as _resolve_ref_by_fov_tp
from well_viewer.core.ui_helpers import safe_int_or_none as _safe_int_or_none
from well_viewer.core.ui_helpers import norm_timepoint
from well_viewer.core.safe import swallowed
# The one background-well parser (ARCHITECTURE §15). This module carried a
# second copy that split on ';' where the shared one did not; the shared one
# takes both now. Kept under the private name the controller re-exports.
from util.well_token import split_background_wells as _split_background_wells
# The region-delete verdict shares its geometry with the crop layer. ``crop`` is
# an always-present support library whose ``region`` module is pure pandas and
# imports nothing back, so this is a plain module-scope dependency, not a cycle.
from well_viewer.features.crop.region import centroids_inside, has_centroid_columns

_logger = logging.getLogger(__name__)

# Keep the on-the-fly preview field cheap: the illumination envelope is smooth,
# so a handful of frames is plenty for the basic/empty-well median estimate.
_FLATFIELD_PREVIEW_MAX_SAMPLES = 8


def review_row_keys_series(ctx, df: pd.DataFrame) -> pd.Series | None:
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


def review_resolve_image_refs(ctx, *, fov_raw: str, tp_raw: str):
    """Resolve (fluor_ref, mask_ref).

    Honors ``image_source``: ``"corrected"`` (default) prefers the top-hat
    output and falls back to raw; ``"raw"`` and ``"flatfield"`` both resolve the
    raw frame (raw is the flat-field input — the renderer applies the flat-field
    to it) and fall back to top-hat. Falling back rather than failing keeps
    Review Image usable on datasets that only ship one of the two image kinds
    for a given FOV/timepoint.

    A8 D4: ctx-native — the image_source + preview ref maps read off typed
    ``ctx.state`` and the timepoint normaliser is the ctx ``norm_timepoint`` leaf.
    """
    def _norm_tp(t):
        return norm_timepoint(ctx, t)
    raw_first = ctx.state.review_image.image_source in ("raw", "flatfield")
    tophat_map = ctx.state.preview.tophat_fluor
    raw_map = ctx.state.preview.fluor
    if raw_first:
        primary, fallback = raw_map, tophat_map
    else:
        primary, fallback = tophat_map, raw_map
    fluor_ref = _resolve_ref_by_fov_tp(
        primary, fov_raw=fov_raw, tp_raw=tp_raw,
        norm_timepoint=_norm_tp,
    )
    if fluor_ref is None:
        fluor_ref = _resolve_ref_by_fov_tp(
            fallback, fov_raw=fov_raw, tp_raw=tp_raw,
            norm_timepoint=_norm_tp,
        )
    mask_ref = _resolve_ref_by_fov_tp(
        ctx.state.preview.mask, fov_raw=fov_raw, tp_raw=tp_raw,
        norm_timepoint=_norm_tp,
    )
    return fluor_ref, mask_ref


def _flatfield_qc(ctx, key: str, default: str = "") -> str:
    info = ctx.state.dataset.pipeline_info or {}
    qc = info.get("qc_options") or {}
    return str(qc.get(key, default) or default).strip()


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


def flatfield_field_for(ctx, channel: str, fluor_arr, tp=None) -> np.ndarray | None:
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
    import pipeline.flatfield as _ffc

    mode = _flatfield_qc(ctx, "flatfield", "none").lower()
    if mode in ("", "none"):
        return None
    if mode == "gaussian":
        # Self-referential (one image) — cheap, recomputed per frame.
        return _ffc.estimate_gaussian_flatfield(fluor_arr)

    chan = str(channel or "").lower()
    from well_viewer.data.image_identity import normalize_tp
    tp_key = normalize_tp(tp) if (mode == "empty_well" and tp is not None) else ""
    cache = ctx.state.review_image.flatfield_field_cache
    key = (chan, mode, tp_key)
    if key in cache:
        return cache[key]

    field = None
    try:
        if mode == "basic":
            ctx.notify.set_status("Computing flat-field (basic) for preview…")
            arrs = _load_arrays_from_refs(
                ctx.state.preview.fluor, limit=_FLATFIELD_PREVIEW_MAX_SAMPLES)
            field = _ffc.estimate_basic_flatfield(arrs) if arrs else None
        elif mode == "empty_well":
            wells = _split_background_wells(_flatfield_qc(ctx, "background_well", ""))
            if wells:
                ctx.notify.set_status("Computing flat-field (empty well) for preview…")
                from well_viewer.data.image_discovery import find_well_images_and_masks
                ds = ctx.state.dataset
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
    except Exception:  # a failed preview field falls back to raw
        _logger.exception("flat-field preview recompute failed (mode=%s)", mode)
        field = None

    cache[key] = field
    return field


def review_build_include_map(
    ctx, mask_arr, well: str, fov: str, tp: str,
) -> dict[int, bool]:
    """Build {nid: is_included} for all labels in ``mask_arr``.

    A8 D4: ctx-native — rows come from the ctx ``get_rows`` service and the
    manual overrides from the live ``ctx.runtime.review.included_overrides``.
    """
    center = _np.asarray(mask_arr)
    include_by_nid: dict[int, bool] = {
        int(nid): True for nid in _np.unique(center) if int(nid) > 0
    }
    df = get_rows(ctx, well)
    if df is not None and not df.empty:
        keys = review_row_keys_series(ctx, df)
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
                overrides = ctx.runtime.review.included_overrides or {}
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
    for (ovr_well, ovr_fov, ovr_tp, ovr_nid), val in (ctx.runtime.review.included_overrides or {}).items():
        if ovr_well != well or ovr_fov != fov or ovr_tp != tp:
            continue
        nid = _safe_int_or_none(ovr_nid)
        if nid is None:
            continue
        if nid in include_by_nid:
            include_by_nid[nid] = (val.strip() != "0")
    return include_by_nid


def review_build_mfi_map(
    ctx, mask_arr, well: str, fov: str, tp: str, channel=None,
) -> dict[int, float]:
    """Build ``{nid: mean fluorescence intensity}`` for cells in the displayed
    FOV/timepoint.

    MFI is the per-cell ``{channel}_mean_intensity`` CSV column for the displayed
    image channel (``active_image_channel`` unless ``channel`` overrides it).
    Returns an empty map when the channel carries no intensity column (e.g. the
    nuc+seg overlay or a ratio pseudo-channel), so callers can silently skip the
    readout. Only nuclei actually present in ``mask_arr`` are reported.
    """
    chan = str(
        channel if channel is not None
        else ctx.state.channel.active_image_channel or ""
    ).strip().lower()
    if not chan:
        return {}
    col = f"{chan}_mean_intensity"
    df = get_rows(ctx, well)
    if df is None or df.empty or col not in df.columns:
        return {}
    keys = review_row_keys_series(ctx, df)
    if keys is None:
        return {}
    present = {int(nid) for nid in _np.unique(_np.asarray(mask_arr)) if int(nid) > 0}
    fov_eq = keys.map(lambda k: k[0] == fov and k[1] == tp and k[2] != "")
    sub = df.loc[fov_eq.fillna(False).to_numpy()]
    if sub.empty:
        return {}
    sub_keys = keys.loc[sub.index]
    nids = sub_keys.map(lambda k: k[2]).map(_safe_int_or_none)
    vals = pd.to_numeric(sub[col], errors="coerce").to_numpy()
    out: dict[int, float] = {}
    for nid, v in zip(nids, vals):
        if nid is None or (present and nid not in present):
            continue
        if _np.isfinite(v):
            out[int(nid)] = float(v)
    return out


def _binary_gate_columns(ctx, channel=None):
    """Return ``(val_col, threshold)`` for the Segmentation binary mask.

    Keyed to the **displayed image channel** (``active_image_channel``) — the
    channel the Segmentation tab is actually showing — so "Binary (≥ ON
    threshold)" binarises the channel you're viewing. The global plotting
    channel (``active_val_col`` / the active-channel threshold) can differ and
    would otherwise pull the wrong channel. Falls back to the plotting column
    when no image channel is set (e.g. the nuc+seg overlay).

    ``channel`` overrides the channel whose value-column + per-channel ON cutoff
    are used — the Image Table passes the *cell's* channel so each cell honours
    its own Sample-Definitions threshold rather than whatever the Segmentation
    tab last displayed. Which cutoff applies follows the column: an
    ``smfish_count`` readout is cut against the spot tally, not a brightness.
    """
    from well_viewer.core.channel_state_controller import on_cutoff_for_column
    if channel is not None:
        img_chan = str(channel).strip().lower()
    else:
        img_chan = str(ctx.state.channel.active_image_channel or "").strip()
    if img_chan:
        col = f"{img_chan}_{ctx.state.channel.active_metric}"
        return (col, on_cutoff_for_column(ctx, col, channel=img_chan))
    val_col = ctx.state.channel.active_val_col
    return (val_col, on_cutoff_for_column(ctx, val_col))


def review_build_threshold_map(
    ctx, mask_arr, well: str, fov: str, tp: str, channel=None,
) -> dict[int, bool]:
    """Build {nid: above_threshold} for cells in the displayed FOV/timepoint.

    Mirrors the bar plot's gating logic: a cell is "above threshold" only when it
    passes (a) cell-area gating, (b) every fluorescence gate, and (c)
    ``row[val_col] > thresh_frac_on``. The value column + ON threshold are taken
    from the **displayed image channel** (``active_image_channel``) — the channel
    the Segmentation tab is actually showing — not the global plotting channel
    (``active_val_col``), which can differ and would otherwise binarise the
    wrong channel.
    """
    from well_viewer.core.channel_state_controller import get_all_fluor_gates
    center = _np.asarray(mask_arr)
    fluor_gates = get_all_fluor_gates(ctx)
    # Gate against the channel being viewed on the Segmentation tab, not the
    # global plotting channel — see _binary_gate_columns.
    val_col, threshold = _binary_gate_columns(ctx, channel)
    ratios = ctx.state.channel.ratio_index
    above_by_nid: dict[int, bool] = {
        int(nid): False for nid in _np.unique(center) if int(nid) > 0
    }
    df = get_rows(ctx, well)
    if df is None or df.empty:
        return above_by_nid
    keys = review_row_keys_series(ctx, df)
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


def apply_review_overrides_to_cache(ctx, label: str | None = None) -> None:
    """Project overrides onto cached row['Included'] so non-Review tabs see them.

    The override dict is the source of truth; this projects values onto the
    cached rows so every controller using row_is_included(row) automatically
    respects user toggles. Called after gating recomputes (which rewrite
    row['Included'] from thresholds), after patch-file load, and on toggle.
    """
    from well_viewer.data.data_loading import get_rows
    overrides = ctx.runtime.review.included_overrides
    if not overrides:
        return
    by_label: dict[str, dict[tuple[str, str, str], str]] = {}
    for (lbl, fov, tp, nid), val in overrides.items():
        if label is not None and lbl != label:
            continue
        by_label.setdefault(lbl, {})[(fov, tp, nid)] = val
    for lbl, lookup in by_label.items():
        if lbl not in ctx.state.dataset.well_paths:
            continue
        try:
            df = get_rows(ctx, lbl)
        except Exception:
            swallowed()  # silent except
            continue
        if df is None or df.empty:
            continue
        keys = review_row_keys_series(ctx, df)
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


def review_row_keys(ctx, row: dict) -> tuple[str, str, str]:
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
        review_norm_fov(_pick("fov", "FOV")),
        norm_timepoint(ctx, _pick("timepoint", "tp", "time", "time_h", "timepoint_hours")),
        review_norm_fov(_pick("nucleus_id", "nucleus id", "nucleusId", "nucleusID")),
    )


# Timepoint ordering is shared app-wide — see util.timepoint.sort_key. The old
# local copy returned 0/1 where the shared one returns False/True; those sort
# identically, so the ordering is unchanged.
from util.timepoint import sort_key as _review_tp_sort_key


def review_collect_timepoints(ctx, fov: str) -> list:
    """Sorted list of timepoints available for ``fov`` (fluor + pipeline).

    A8 D4: ctx-native — the preview ref map + pipeline info read off typed
    ``ctx.state`` and normalisation uses the pure ``review_norm_fov`` /
    ``norm_timepoint`` leaves.
    """
    def _norm(f):
        return review_norm_fov(f)
    tp_values = sorted(
        {
            norm_timepoint(ctx, tp)
            for (f, tp) in ctx.state.preview.fluor.keys()
            if _norm(f) == fov and norm_timepoint(ctx, tp)
        },
        key=_review_tp_sort_key,
    )
    pipeline_tp_values = sorted(
        {
            norm_timepoint(ctx, tp)
            for tp in (ctx.state.dataset.pipeline_info or {}).get("available_timepoints", [])
            if norm_timepoint(ctx, tp)
        },
        key=_review_tp_sort_key,
    )
    if pipeline_tp_values:
        tp_values = sorted(set(tp_values) | set(pipeline_tp_values), key=_review_tp_sort_key)
    return tp_values


def review_load_rows(ctx, label: str) -> list[dict]:
    """Materialize the cached DataFrame for ``label`` as a list of row dicts.

    Used by the Review CSV table widget and the Review Image overlay
    builders. Mutations to returned dicts are local — write back to the
    canonical store via ``_apply_review_overrides_to_cache`` (which
    updates the cached DataFrame's ``Included`` column directly).

    A8 E4-review: ctx-native — row materialisation goes through the shared
    ``data_loading.get_rows`` service.
    """
    try:
        df = get_rows(ctx, label)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    return df.to_dict("records")


#: Column spellings the Review CSV has to tolerate, in preference order. The
#: table is fed from whatever the pipeline wrote, and older outputs used the
#: shorter timepoint names.
_FOV_COLS = ("fov", "FOV")
_TP_COLS = ("timepoint", "tp", "time")


def _first_col(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    return next((c for c in names if c in df.columns), None)


def any_review_rows(ctx, labels) -> bool:
    """Whether any of *labels* has a non-empty cached frame.

    The question the Review CSV used to answer with ``if not rows:`` over a
    fully materialised pool. It only ever needed to know whether the pool would
    have been empty.
    """
    for label in labels:
        try:
            df = get_rows(ctx, label)
        except Exception:
            swallowed(f"review rows for {label!r}")
            continue
        if df is not None and not df.empty:
            return True
    return False


def review_filter_values(ctx, labels) -> tuple[list[str], list[str]]:
    """The FOV and timepoint values the Review CSV combos offer, for *labels*.

    Derived from each frame's **distinct** column values rather than from a
    pooled list of row dicts. A well has on the order of ten FOVs and a handful
    of timepoints however many cells it holds, so this is a per-well constant
    where the previous shape was one Python dict per cell across the whole
    selection — see :func:`review_pooled_rows`.

    Both values are normalised the same way the row filter and the cell-click
    navigation normalise them (``review_norm_fov`` / ``norm_timepoint``), which
    is what makes a combo entry selectable: the combo is non-editable, so a
    raw ``"1.0"`` item against a ``setCurrentText("1")`` click silently no-ops.
    """
    fovs: set[str] = set()
    tps: list[str] = []
    seen_tps: set[str] = set()
    for label in labels:
        try:
            df = get_rows(ctx, label)
        except Exception:
            swallowed(f"review rows for {label!r}")
            continue
        if df is None or df.empty:
            continue
        fov_col = _first_col(df, _FOV_COLS)
        if fov_col is not None:
            for raw in pd.unique(df[fov_col]):
                norm = review_norm_fov(raw)
                if norm:
                    fovs.add(norm)
        tp_col = _first_col(df, _TP_COLS)
        if tp_col is not None:
            for raw in pd.unique(df[tp_col]):
                norm = norm_timepoint(ctx, str(raw).strip())
                if norm and norm not in seen_tps:
                    seen_tps.add(norm)
                    tps.append(norm)
    from util.fov import sort_key as _fov_sort_key

    return (sorted(fovs, key=_fov_sort_key),
            sorted(tps, key=_review_tp_sort_key))


def _match_mask(ctx, df: pd.DataFrame, fov_sel: str, tp_sel: str):
    """Boolean mask of *df*'s rows passing the FOV / timepoint filters.

    An empty selection matches everything, which is what the combos mean when
    they carry no value.
    """
    mask = pd.Series(True, index=df.index)
    if fov_sel:
        fov_col = _first_col(df, _FOV_COLS)
        if fov_col is None:
            return pd.Series(False, index=df.index)
        mask &= df[fov_col].map(review_norm_fov) == fov_sel
    if tp_sel:
        tp_col = _first_col(df, _TP_COLS)
        if tp_col is None:
            return pd.Series(False, index=df.index)
        mask &= df[tp_col].map(lambda v: norm_timepoint(ctx, str(v).strip())) == tp_sel
    return mask


def review_pooled_rows(ctx, labels, *, fov_sel: str = "", tp_sel: str = "",
                       limit: int | None = None) -> tuple[list[dict], int]:
    """Matching rows for *labels* as dicts, materialising at most *limit* of them.

    Returns ``(rows, n_matched)`` — ``n_matched`` is the true total, counted
    vectorised, so a caller can report an honest "showing N of M" without
    holding M of anything.

    **The limit is the point.** The Review CSV table is an inspection aid for
    one FOV/timepoint and caps itself at a few thousand rows, but it used to
    reach that cap by first building one Python dict per cell for every
    selected well and then slicing. Measured on a 24-well plate of 28,800 rows
    and 40 columns, that pooled list costs ~200 MB resident — about 7 KB per
    row — and it scales with the plate, so a full plate of several hundred
    thousand cells reaches several gigabytes before the cap is consulted. The
    controller's own comment already described the unbounded build as "the OOM
    that SIGKILLs the app"; the cap it added protected the ``QTableWidget``
    below it and not the list above it.

    Filtering happens on the frame, so an unmatched well contributes nothing at
    all rather than contributing every row it has.
    """
    rows: list[dict] = []
    matched = 0
    for label in labels:
        try:
            df = get_rows(ctx, label)
        except Exception:
            swallowed(f"review rows for {label!r}")
            continue
        if df is None or df.empty:
            continue
        sub = df[_match_mask(ctx, df, fov_sel, tp_sel)]
        matched += len(sub)
        if limit is not None and len(rows) >= limit:
            continue          # keep counting; stop materialising
        if limit is not None:
            sub = sub.head(limit - len(rows))
        rows.extend(sub.to_dict("records"))
    return rows, matched


def review_effective_included(ctx, label: str, row: dict) -> str:
    """Effective Included value for a cached row.

    User overrides set via the Review Image tab take precedence over the
    gating-computed ``Included`` field stored on the cached row. Overrides
    are kept in a separate dict so they survive subsequent gating recomputes
    (which rewrite the canonical row['Included']).

    A8 E4-review: ctx-native — the (fov,tp,nid) key comes from the ctx
    ``review_row_keys`` leaf and the manual override map is the live
    ``ctx.runtime.review.included_overrides`` scratch.
    """
    fov, tp, nid = review_row_keys(ctx, row)
    val = None
    if fov and tp and nid:
        overrides = ctx.runtime.review.included_overrides or {}
        ovr = overrides.get((label, fov, tp, nid))
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


def collect_region_delete_targets(
    ctx, region, *, wells, fovs, tps, skip=None,
):
    """Resolve which cells a drawn rectangle deletes across a scope of frames.

    ``region`` is a ``(row0, col0, row1, col1)`` rectangle in source-mask pixels
    — the same normalised tuple :func:`well_viewer.features.crop.region.normalize_region`
    produces. ``wells`` / ``fovs`` / ``tps`` are the concrete target sets (the
    caller has already resolved "the displayed one" into a value), and ``skip``
    is an optional ``(well, fov, tp)`` frame to leave out, used for the frame the
    box was actually drawn on: that one is deleted from its label mask instead,
    by any-pixel overlap rather than by centroid.

    Membership here is centroid-inside, shared with the crop layer via
    :func:`~well_viewer.features.crop.region.centroids_inside`. It is the only
    rule available off-screen — deciding overlap for a frame that is not
    displayed would mean decoding that frame's mask out of its ``_out.zip``,
    which is one decode per well × FOV × timepoint. Reading the centroid columns
    the CSV already carries makes a plate-wide apply a pandas pass instead.

    Fail-closed: a cell whose centroid is missing or NaN is never a target, and a
    well whose CSV carries no ``shape_centroid_*`` columns at all contributes
    nothing and is named in the returned stats so the caller can say so out loud.

    Returns ``(targets, stats)`` where ``targets`` is a list of canonical
    ``(well, fov, tp, nid)`` keys — built through
    :func:`~well_viewer.data.image_identity.cell_keys_for_dataframe`, so they
    match the ``included_overrides`` map and ``cell_overrides.json`` exactly —
    and ``stats`` is a dict with ``frames`` (distinct frames contributing at
    least one target), ``wells`` (likewise for wells), ``scanned_wells``,
    ``no_centroid_wells`` and ``unreadable_wells``.
    """
    from well_viewer.data.image_identity import cell_keys_for_dataframe

    targets: list[tuple[str, str, str, str]] = []
    stats = {
        "frames": 0,
        "wells": 0,
        "scanned_wells": 0,
        "no_centroid_wells": [],
        "unreadable_wells": [],
        "unknown_wells": [],
    }
    if region is None or not wells:
        return targets, stats

    want_fovs = {str(f) for f in (fovs or ())}
    want_tps = {str(t) for t in (tps or ())}
    if not want_fovs or not want_tps:
        return targets, stats

    frames: set[tuple[str, str, str]] = set()
    hit_wells: set[str] = set()
    for well in wells:
        if well not in ctx.state.dataset.well_paths:
            # An armed scope can outlive the wells it named (a scope carried
            # from a hand-typed selection, a well removed from the folder).
            # Record it so the caller can say so instead of quietly deleting
            # from fewer wells than the button advertises.
            stats["unknown_wells"].append(well)
            continue
        stats["scanned_wells"] += 1
        try:
            df = get_rows(ctx, well)
        except Exception:
            _logger.warning(
                "Region delete: could not read rows for well %r", well, exc_info=True,
            )
            stats["unreadable_wells"].append(well)
            continue
        if df is None or df.empty:
            continue
        if not has_centroid_columns(df):
            stats["no_centroid_wells"].append(well)
            continue
        # Slice to the cells inside the box *before* building keys. Key
        # construction is the dominant cost of this whole path — a plate-wide
        # scope over 288k rows spends ~1.6s of its 1.7s here — and the box
        # normally holds a small fraction of the plate, so narrowing first is
        # the difference between a stutter and a multi-second freeze.
        inside = df.loc[centroids_inside(df, region)]
        if inside.empty:
            continue
        for key in cell_keys_for_dataframe(inside, default_well=well):
            if key.fov not in want_fovs or key.tp not in want_tps:
                continue
            if not (key.fov and key.tp and key.nid):
                continue
            # The well is the dataset's own label, uncanonicalised — see the
            # note in ``callbacks.override_key``; every reader keys off it.
            frame = (well, key.fov, key.tp)
            if skip is not None and frame == tuple(skip):
                continue
            targets.append((well, key.fov, key.tp, key.nid))
            frames.add(frame)
            hit_wells.add(well)

    stats["frames"] = len(frames)
    stats["wells"] = len(hit_wells)
    return targets, stats


def review_norm_fov(v: object) -> str:
    """The review side's name for the shared numeric-token normaliser.

    FOV tokens reach the viewer from filenames, CSV columns and combo text, so
    ``1``, ``01`` and ``1.0`` all denote the same field of view and have to
    collapse to one key before anything compares them. That collapsing rule is
    :func:`well_viewer.data.image_resolver.normalize_numeric_token`; this name
    is kept because the image-table and review-image call sites are written
    against it.
    """
    return normalize_numeric_token(v)
