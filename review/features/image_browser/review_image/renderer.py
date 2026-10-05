"""Review-image preview pipeline (image load → render → display).

Extracted from ``ReviewApp`` so the GUI class no longer owns image
decoding, boundary computation, or pixmap composition. Each function takes
``app`` (ReviewApp) for state access.
"""

from __future__ import annotations

import html as _html
import logging

from ui import theme
from util.fov import sort_key as fov_sort_key
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from core import debug_flags as _debug_flags
from review.data.data_loading import extract_well_token as _extract_well_token
# collect.py is the (Qt-free) data half of this feature and never imports the
# renderer, so these resolve at module scope — no deferred cycle-break needed.
from review.features.image_browser.review_image.collect import (
    EXCLUSION_LAYERS,
    flatfield_field_for,
    review_build_exclusion_reasons,
    review_build_include_map,
    review_build_mfi_map,
    review_build_threshold_map,
    review_collect_timepoints,
    review_norm_fov,
    review_resolve_image_refs,
)

_logger = logging.getLogger("review")

try:
    import numpy as _np
    _NP_AVAILABLE = True
except ImportError:
    _np = None  # type: ignore[assignment]
    _NP_AVAILABLE = False

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def update_preview(win, well_label: str | None) -> None:
    """Legacy win-typed entry: bridge to the ctx-native preview pipeline.

    Kept so the still-win callers (registry ``_activate_segmentation``,
    ``preview_view``, ``channel_state_controller``) call it unchanged.
    """
    update_preview_ctx(win._ctx, well_label)


def update_preview_ctx(ctx, well_label: str | None) -> None:
    """Load images for *well_label* and render the inline montage.

    Same re-entry guard as refresh_review_image: update_preview drives the FOV
    combo, whose signal handler calls back into the review-image refresh path,
    which can re-enter update_preview through the channel / well plate callbacks.
    """
    if ctx.state.preview.update_in_progress:
        return
    ctx.state.preview.update_in_progress = True
    try:
        _update_preview_impl(ctx, well_label)
    finally:
        ctx.state.preview.update_in_progress = False


def _update_preview_impl(ctx, well_label: str | None) -> None:
    # Local imports keep this module standalone-importable without paying
    # the cost of dragging in runtime_app at module load time.
    from review.core.ui_helpers import set_combo_values as _set_combo_values

    widgets = ctx.widgets
    state = ctx.state
    # Switching wells keeps the current zoom/pan — browsing well-to-well
    # shouldn't snap the view back to fit each time. (FOV / timepoint / channel
    # changes go through refresh_review_image directly and still reset.)
    state.review_image.preserve_view_on_refresh = True

    channel_switch_debug = _debug_flags.review_image_channel_switch_debug_enabled()
    if well_label is None:
        well_lbl = widgets.get_or_none("_preview_well_lbl")
        if well_lbl is not None:
            well_lbl.setText("No well selected")
        ri_well_lbl = widgets.get_or_none("_review_image_well_lbl")
        if ri_well_lbl is not None:
            ri_well_lbl.setText("No well selected")
        fov_menu = widgets.get_or_none("_review_image_fov_menu")
        if fov_menu is not None:
            _set_combo_values(fov_menu, ["—"])
        tp_menu = widgets.get_or_none("_review_image_tp_menu")
        if tp_menu is not None:
            _set_combo_values(tp_menu, ["—"])
            tp_menu.setCurrentText("—")
        state.preview.fluor = state.preview.overlay = state.preview.mask = {}
        status = widgets.get_or_none("_review_image_status")
        if status is not None:
            status.setText("Select a well in the left panel.")
        if channel_switch_debug:
            _logger.debug("[RI-CHSW step 4] update_preview early return: no well selected")
        return

    tok = _extract_well_token(well_label) or well_label
    well_lbl = widgets.get_or_none("_preview_well_lbl")
    if well_lbl is not None:
        well_lbl.setText(tok)
    ri_well_lbl = widgets.get_or_none("_review_image_well_lbl")
    if ri_well_lbl is not None:
        ri_well_lbl.setText(tok)

    active_image_channel = ""
    try:
        active_image_channel = str(state.channel.active_image_channel or "").strip().lower()
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 4] update_preview start well=%r active_channel=%r",
                well_label, active_image_channel,
            )
        fluor, overlay, mask, tophat_fluor = state.images.images_for_well(
            well_label, active_image_channel)
    except Exception as _exc:
        _logger.exception("Unexpected error searching images for %r: %s", well_label, _exc)
        fluor, overlay, mask, tophat_fluor = {}, {}, {}, {}
    state.preview.fluor = fluor
    state.preview.overlay = overlay
    state.preview.mask = mask
    state.preview.tophat_fluor = tophat_fluor
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 4] update_preview refs loaded well=%r active_channel=%r fluor=%d tophat=%d overlay=%d mask=%d",
            well_label, active_image_channel,
            len(fluor), len(tophat_fluor), len(overlay), len(mask),
        )

    all_fovs = sorted(
        {
            fov_norm
            for refs in (fluor, overlay, mask, tophat_fluor)
            for (fov, _tp) in refs.keys()
            for fov_norm in [review_norm_fov(fov)]
            if fov_norm
        },
        key=fov_sort_key,
    )
    if channel_switch_debug:
        fov_var = widgets.get_or_none("_preview_fov_var")
        _logger.debug(
            "[RI-CHSW step 4] update_preview candidate_fovs=%s selected_fov_before=%r",
            all_fovs,
            fov_var.currentText() if fov_var is not None else "—",
        )

    if not (fluor or overlay or mask or tophat_fluor):
        tok = _extract_well_token(well_label) or well_label
        dirs = f"in={state.dataset.in_dir}  out={state.dataset.data_dir}"
        msg = f"No images found for {tok}. Searched: {dirs}"
        _logger.warning(msg)
        # An empty result and a scan that failed before it read anything are two
        # different answers, and this path used to leave the *previous* well's
        # status text on screen for both — so the canvas showed one well and the
        # line beneath it described another. Say which of the two happened, here,
        # where the draw that would normally rewrite the line never runs.
        status = widgets.get_or_none("_review_image_status")
        if status is not None:
            shortfall = _scan_shortfall_suffix(ctx)
            status.setText(
                (f"No images could be read for {tok}.{shortfall}") if shortfall
                else f"No images found for {tok}. Searched: {dirs}"
            )
        return

    fov_menu = widgets.get_or_none("_review_image_fov_menu")
    if fov_menu is not None:
        _set_combo_values(fov_menu, all_fovs or ["—"])

    if channel_switch_debug:
        _logger.debug("[RI-CHSW step 4->6] triggering refresh_review_image after preview reload")
    refresh_review_image_ctx(ctx)


def refresh_review_image_ctx(ctx) -> None:
    """ctx-native entry point for the Review Image refresh.

    A8 D4: the whole draw web (``collect`` data helpers + the pixmap-service
    leaves) is ctx-native, so this walks ``ctx`` directly — no app bridge. The
    re-entry guard flag lives on the review-image runtime scratch.
    """
    from review.core.ui_helpers import set_combo_values as _set_combo_values

    if ctx.runtime.review_image.label is None:
        return
    # Re-entry guard: refresh_review_image programmatically updates the
    # FOV / TP combos, whose currentIndexChanged signal re-invokes this
    # function. Without the guard, a Review-CSV double-click that walks
    # tab/sub-tab/FOV/TP all at once can stack signal callbacks deep
    # enough to trip RecursionError. Skip the nested call -- the outer
    # invocation will redraw with the final state.
    if ctx.runtime.review_image.refresh_in_progress:
        return
    ctx.runtime.review_image.refresh_in_progress = True
    try:
        _refresh_review_image_impl(ctx, _set_combo_values, channel_switch_debug=_debug_flags.review_image_channel_switch_debug_enabled(), image_load_debug=_debug_flags.review_image_load_debug_enabled())
    finally:
        ctx.runtime.review_image.refresh_in_progress = False


def refresh_review_image(win) -> None:
    """App-native compatibility entry — bridge to the ctx-native refresh.

    Kept so the many still-win callers (renderer toggles, controller cell
    edits, crop / registry / preview-panel / channel-switch handlers) invoke it
    unchanged; the body reaches the context via ``win._ctx`` (the win→ctx
    direction), never the ``win``-escape-hatch field on the context."""
    refresh_review_image_ctx(win._ctx)


def _resolve_review_fov_tp(ctx, widgets, status, _set_combo_values, *, well, channel_switch_debug: bool) -> tuple | None:
    """Resolve + validate the active (fov_raw, fov, tp_raw, tp) for the redraw.

    Drives the FOV/TP combos, normalises both tokens, and writes the abort
    status line + debug log on an invalid selection. Returns the 4-tuple, or
    ``None`` when the FOV or timepoint is missing (caller then aborts the draw).
    Split out of ``_refresh_review_image_impl`` to keep that orchestrator's
    cyclomatic complexity in check (A8 D4)."""
    from review.core.ui_helpers import norm_timepoint as _norm_timepoint

    fov_raw = ""
    fov_menu = widgets.get_or_none("_review_image_fov_menu")
    if fov_menu is not None:
        fov_raw = str(fov_menu.currentText() or "").strip()
    if not fov_raw:
        fov_var = getattr(ctx.window, "_preview_fov_var", None)
        if fov_var is not None:
            fov_raw = str(fov_var.currentText() or "").strip()
    fov = review_norm_fov(fov_raw)
    if not fov_raw or fov_raw == "—" or not fov:
        if status is not None:
            status.setText("No FOV selected.")
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 6] refresh_review_image aborted: invalid fov raw=%r norm=%r",
                fov_raw, fov,
            )
        return None

    tp_values = review_collect_timepoints(ctx, fov)
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 6] refresh_review_image start well=%r selected_fov_raw=%r normalized_fov=%r active_channel=%r",
            well, fov_raw, fov, ctx.state.channel.active_image_channel,
        )
    tp_menu = widgets.get_or_none("_review_image_tp_menu")
    _set_combo_values(tp_menu, tp_values or ["—"])
    if tp_values and tp_menu.currentText() not in tp_values:
        tp_menu.setCurrentText(tp_values[0])
    tp_raw = str(tp_menu.currentText() or "").strip()
    tp = _norm_timepoint(ctx, tp_raw)
    if not tp_raw or tp_raw == "—" or not tp:
        if status is not None:
            status.setText("No timepoint selected.")
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 6] refresh_review_image aborted: invalid timepoint raw=%r norm=%r",
                tp_raw, tp,
            )
        return None
    return fov_raw, fov, tp_raw, tp


def _refresh_review_image_impl(ctx, _set_combo_values, *, channel_switch_debug: bool, image_load_debug: bool) -> None:
    from review.core.channel_state_controller import (
        get_all_fluor_gates, on_cutoff_for_column,
    )

    widgets = ctx.widgets
    state = ctx.state
    well = state.preview.selected_well
    status = widgets.get_or_none("_review_image_status")
    if well is None:
        if channel_switch_debug:
            _logger.debug("[RI-CHSW step 6] refresh_review_image aborted: no selected well")
        return

    resolved = _resolve_review_fov_tp(
        ctx, widgets, status, _set_combo_values,
        well=well, channel_switch_debug=channel_switch_debug,
    )
    if resolved is None:
        return
    fov_raw, fov, tp_raw, tp = resolved

    fluor_ref, mask_ref = review_resolve_image_refs(ctx, fov_raw=fov_raw, tp_raw=tp_raw)
    if image_load_debug:
        fluor_path = getattr(fluor_ref, "full_path_str", str(fluor_ref)) if fluor_ref is not None else None
        mask_path = getattr(mask_ref, "full_path_str", str(mask_ref)) if mask_ref is not None else None
        _debug_flags.debug_with_source(
            _logger, "Review Image load attempt well=%s fov=%s tp=%s fluor_path=%s",
            well, fov, tp, fluor_path,
        )
        _debug_flags.debug_with_source(
            _logger, "Review Image load attempt well=%s fov=%s tp=%s mask_path=%s",
            well, fov, tp, mask_path,
        )
    if fluor_ref is None or mask_ref is None:
        if status is not None:
            status.setText(
                "Missing fluorescence image or label map for selected FOV/timepoint."
            )
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 6] refresh_review_image missing refs fluor_ref=%r mask_ref=%r",
                fluor_ref, mask_ref,
            )
        return
    state.review_image.is_tif = str(getattr(fluor_ref, "name", "")).lower().endswith((".tif", ".tiff"))
    if not _NP_AVAILABLE:
        if status is not None:
            status.setText("Could not render review image (numpy unavailable).")
        return

    # The "flatfield" stage and "raw" stage resolve the SAME raw file ref, so
    # the source mode must be part of the cache key or they would collide.
    image_source = state.review_image.image_source
    cache_key = (
        getattr(fluor_ref, "full_path_str", id(fluor_ref)),
        getattr(mask_ref, "full_path_str", id(mask_ref)),
        image_source,
    )
    cached = state.review_image.frame_cache
    if cached is not None and cached.get("key") == cache_key:
        fluor_arr = cached["fluor_arr"]
        center = cached["center"]
        boundary = cached["boundary"]
    else:
        fluor_raw = state.images.array(fluor_ref, greyscale=True)
        mask_raw = state.images.array(mask_ref, greyscale=True)
        if fluor_raw is None or mask_raw is None:
            if status is not None:
                status.setText("Could not render review image (image decode failed).")
            return
        fluor_arr = _np.asarray(fluor_raw, dtype=_np.float32)
        # Flat-field validation view: apply ONLY the selected flat-field to the
        # raw frame (recomputed on the fly), before any background/top-hat. A
        # no-op when the mode resolves to no field (e.g. flatfield="none").
        if image_source == "flatfield":
            import pipeline.flatfield as _ffc
            field = flatfield_field_for(
                ctx, str(state.channel.active_image_channel or ""), fluor_arr,
                tp=tp, well=well)
            fluor_arr = _np.asarray(_ffc.apply_flatfield(fluor_arr, field), dtype=_np.float32)
        center_int = _np.rint(_np.asarray(mask_raw)).astype(_np.int32, copy=False)
        padded = _np.pad(center_int, 1, mode="constant", constant_values=0)
        center = padded[1:-1, 1:-1]
        boundary = (center > 0) & (
            (center != padded[:-2, 1:-1]) |
            (center != padded[2:, 1:-1]) |
            (center != padded[1:-1, :-2]) |
            (center != padded[1:-1, 2:])
        )
        state.review_image.frame_cache = {
            "key": cache_key,
            "fluor_arr": fluor_arr,
            "center": center,
            "boundary": boundary,
        }

    ic_key = (well, fov, tp, state.review_image.override_version)
    include_by_nid = state.review_image.include_cache.get(ic_key)
    if include_by_nid is None:
        include_by_nid = review_build_include_map(ctx, center, well, fov, tp)
        if len(state.review_image.include_cache) > 32:
            state.review_image.include_cache.clear()
        state.review_image.include_cache[ic_key] = include_by_nid

    # Which layer removed each excluded cell, for the "Why removed" overlay
    # and the hover readout. Built only when the overlay asks for it, since it
    # reads the well's rows; the include map above is derived from the same
    # function, so the two cannot disagree about who is out.
    reasons_by_nid = None
    if getattr(state.review_image, "show_exclusion_layers", False):
        reasons_by_nid = review_build_exclusion_reasons(ctx, center, well, fov, tp)
    # Published for the hover readout. An outline can only be one colour, so
    # the picture can name one layer per cell and no more -- and three
    # docstrings and the button's own tooltip promised a readout that "names
    # every layer that dropped it" while no such readout existed anywhere: the
    # hover emitted position, channel value, cell id and MFI and nothing about
    # exclusion, so a cell failing three layers was a dead end. The tuple
    # ``collect`` goes to the trouble of building was discarded except for
    # ``layers[0]``.
    state.review_image.exclusion_reasons_by_nid = reasons_by_nid

    # Per-cell MFI for the hover tooltip — the displayed channel's
    # {chan}_mean_intensity keyed by nucleus id. Rebuilt each refresh so it
    # tracks the active channel / FOV / timepoint; empty for non-fluor overlays.
    state.review_image.mfi_by_nid = review_build_mfi_map(ctx, center, well, fov, tp)

    above_by_nid: dict[int, bool] | None = None
    if bool(state.review_image.binary_mask):
        fluor_gates = get_all_fluor_gates(ctx)
        # Part of the cache key, so it has to be the cutoff
        # ``review_build_threshold_map`` will actually apply — otherwise a
        # cached map computed under a different cutoff is served back.
        threshold = on_cutoff_for_column(ctx, state.channel.active_val_col)
        tm_key = (
            well, fov, tp, state.review_image.override_version,
            state.channel.active_val_col, threshold,
            tuple(sorted(fluor_gates.items())),
        )
        above_by_nid = state.review_image.threshold_map_cache.get(tm_key)
        if above_by_nid is None:
            above_by_nid = review_build_threshold_map(ctx, center, well, fov, tp)
            if len(state.review_image.threshold_map_cache) > 32:
                state.review_image.threshold_map_cache.clear()
            state.review_image.threshold_map_cache[tm_key] = above_by_nid

    preserve_view = bool(state.review_image.preserve_view_on_refresh)
    state.review_image.preserve_view_on_refresh = False
    if channel_switch_debug:
        _logger.debug("[RI-CHSW step 6->7] draw_review_image preserve_view=%s", preserve_view)
    draw_review_image(
        ctx,
        fluor_arr, center, include_by_nid,
        fit_lut=False, preserve_view=preserve_view, boundary=boundary,
        above_by_nid=above_by_nid, exclusion_reasons=reasons_by_nid,
    )


def _scan_shortfall_suffix(ctx) -> str:
    """The status-line clause naming a truncated image scan, or ``""``.

    ``find_well_images_and_masks`` returns
    :class:`~review.data.zip_members.ScanResult` inventories that carry
    their own provenance: ``partial`` is True when the walk over the archive or
    folder stopped early, and ``reason`` says why. A truncated inventory is the
    same shape as a complete one — a short dict of real images — so the FOV and
    timepoint menus built from it are simply shorter, with nothing on screen to
    distinguish "this well holds two fields" from "the scan reached two fields
    and then hit a bad CRC". Reading the flag here is what puts the difference
    where the user is already looking.

    The four maps are checked together because any one of them being short
    makes the displayed frame potentially the wrong one: a missing mask draws no
    boundaries, a missing top-hat frame silently falls back to raw.
    """
    st = getattr(ctx, "state", None)
    preview = getattr(st, "preview", None)
    if preview is None:
        return ""
    maps = (
        getattr(preview, "fluor", None),
        getattr(preview, "tophat_fluor", None),
        getattr(preview, "overlay", None),
        getattr(preview, "mask", None),
    )
    reasons = [
        str(getattr(m, "reason", "") or "")
        for m in maps
        if getattr(m, "partial", False)
    ]
    if not reasons:
        return ""
    why = "; ".join(dict.fromkeys(r for r in reasons if r)) or "a scan stopped short"
    return (
        "  ·  WARNING: this well's image list is INCOMPLETE — "
        f"{why}. Fields or timepoints the well holds may be missing from the "
        "menus above, so what is shown is not the whole well."
    )


def draw_review_image(
    ctx,
    fluor_arr,
    mask_arr,
    include_by_nid: dict[int, bool],
    *,
    fit_lut: bool = False,
    preserve_view: bool = False,
    boundary=None,
    above_by_nid: dict[int, bool] | None = None,
    exclusion_reasons: dict[int, tuple] | None = None,
) -> None:
    # A8 D4: ctx-native pixmap compositor. Typed view/colour state lives on
    # ``ctx.state.review_image`` / ``ctx.state.image_view``; the QLabel host +
    # decoded RGB buffer are the ``ctx.runtime.review_image`` scratch; the LUT
    # boxes / status label are Qt widgets reached through ``ctx.widgets`` (the
    # registered status label) or ``ctx.window`` (the unregistered LUT entries).
    from review.core.channel_state_controller import active_channel_label
    state = ctx.state
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] draw_review_image channel=%r fit_lut=%s preserve_view=%s",
            state.channel.active_image_channel,
            fit_lut, preserve_view,
        )
    arr = _np.asarray(fluor_arr, dtype=_np.float32)
    state.review_image.last_fluor_arr = arr
    m = _np.asarray(mask_arr)
    if fit_lut:
        lo, hi = float(arr.min()), float(arr.max())
        if hi <= lo:
            hi = lo + 1.0
        state.image_view.lut_by_channel[str(state.channel.active_image_channel or "").lower()] = (lo, hi)
    else:
        lo, hi = review_image_resolve_lut(ctx, arr)
    lut_chan_lbl = getattr(ctx.window, "_review_lut_chan_lbl", None)
    if lut_chan_lbl is not None:
        lut_chan_lbl.setText(f"{state.channel.active_image_channel.upper()} LUT min:")
    # Keep the min/max boxes in sync with the LUT actually applied, so the
    # user always sees the real numbers (not a stale "auto") and edits start
    # from the current range. (These are ``_review_lmin_entry`` /
    # ``_review_lmax_entry`` — the previous ``_review_lut_min_edit`` names
    # never matched a real widget, so the boxes never updated.)
    lmin_entry = getattr(ctx.window, "_review_lmin_entry", None)
    lmax_entry = getattr(ctx.window, "_review_lmax_entry", None)
    if lmin_entry is not None and lmax_entry is not None:
        for _entry, _val in ((lmin_entry, lo), (lmax_entry, hi)):
            _blocked = _entry.blockSignals(True)
            try:
                _entry.setText(f"{_val:.0f}")
            finally:
                _entry.blockSignals(_blocked)
    base = ((_np.clip(arr, lo, hi) - lo) / (hi - lo) * 255).astype(_np.uint8)
    h, w = base.shape

    if m.dtype != _np.int32 or m.ndim != 2:
        center = _np.rint(m).astype(_np.int32, copy=False)
    else:
        center = m
    if boundary is None:
        padded = _np.pad(center, 1, mode="constant", constant_values=0)
        center_view = padded[1:-1, 1:-1]
        boundary = (center_view > 0) & (
            (center_view != padded[:-2, 1:-1]) |
            (center_view != padded[2:, 1:-1]) |
            (center_view != padded[1:-1, :-2]) |
            (center_view != padded[1:-1, 2:])
        )

    # ``above_by_nid is None`` means the per-cell threshold could not be
    # EVALUATED for this field — no rows cover it, the row keys did not resolve,
    # the well has no rows at all (``review_build_threshold_map`` returns None
    # for each). Coercing that to ``{}`` painted the identical all-black
    # silhouette the renderer paints for "measured, and no cell passed", which
    # is a measurement. So the binary view is declined instead: the ordinary
    # intensity image is drawn and the status line says the threshold is
    # unmeasured, which nothing can mistake for a plate of negative cells.
    threshold_unmeasured = bool(state.review_image.binary_mask) and above_by_nid is None
    binary_mask_mode = bool(state.review_image.binary_mask) and not threshold_unmeasured
    sel_nid = state.review_image.selected_nucleus

    if binary_mask_mode:
        rgb = _np.zeros((h, w, 3), dtype=_np.uint8)
        above_ids = [int(nid) for nid, ab in above_by_nid.items() if ab]
        if above_ids:
            above_arr = _np.fromiter(above_ids, dtype=center.dtype, count=len(above_ids))
            above_mask = _np.isin(center, above_arr)
            rgb[above_mask] = _np.array([255, 255, 255], dtype=_np.uint8)
    else:
        tint = state.review_image.tint_color
        tr, tg, tb = (max(0, min(255, int(c))) for c in tint)
        rgb = _np.empty((h, w, 3), dtype=_np.uint8)
        if (tr, tg, tb) == (255, 255, 255):
            rgb[..., 0] = base
            rgb[..., 1] = base
            rgb[..., 2] = base
        else:
            base_f = base.astype(_np.float32)
            rgb[..., 0] = _np.clip(base_f * (tr / 255.0), 0, 255).astype(_np.uint8)
            rgb[..., 1] = _np.clip(base_f * (tg / 255.0), 0, 255).astype(_np.uint8)
            rgb[..., 2] = _np.clip(base_f * (tb / 255.0), 0, 255).astype(_np.uint8)

        included_ids = [int(nid) for nid, inc in include_by_nid.items() if inc]
        if included_ids:
            included_arr = _np.fromiter(included_ids, dtype=center.dtype, count=len(included_ids))
            include_mask = _np.isin(center, included_arr)
        else:
            include_mask = _np.zeros(center.shape, dtype=bool)
        show_outline = bool(state.review_image.show_outline)
        if show_outline:
            draw_boundary = boundary & include_mask
            boundary_color = _np.asarray(state.review_image.boundary_color, dtype=_np.uint8)
            rgb[draw_boundary] = boundary_color
        # Excluded cells get an outline too, in the ink of the layer that
        # removed them -- the only place in the app that says WHICH one did.
        # Drawn whether or not the kept cells' outline is on, because the
        # question "why is this one gone" is asked of a picture where the
        # answer is invisible by construction.
    # **Both modes, once, outside the branch.** The overlay used to be drawn
    # inside the ``else`` above, so turning it on in Binary mode computed every
    # reason -- a full row fetch and verdict walk per refresh -- and then threw
    # them away, while the button went on reading "Why removed: On". A toggle
    # that is on and does nothing is §7.6 exactly, and Binary is the mode where
    # the question is asked most, because a binary mask is already a picture of
    # which cells counted. Hoisting it here is what makes that unrepeatable:
    # there is no longer a branch that can forget to call it, which is a
    # stronger guarantee than a test per branch. The structure is pinned by
    # ``test_exclusion_layers_overlay``, so a later re-branch has to argue with
    # a guard rather than pass quietly.
    _paint_exclusion_outlines(
        rgb, boundary, center, exclusion_reasons,
        on=bool(getattr(state.review_image, "show_exclusion_layers", False)))
    # Last, so the cell being inspected is never hidden under a layer ink --
    # in either mode, which the branched version also had to get right twice.
    if sel_nid is not None:
        sel_color = _np.asarray(state.review_image.selected_color, dtype=_np.uint8)
        sel_boundary = boundary & (center == int(sel_nid))
        rgb[sel_boundary] = sel_color

    _draw_crop_outline(ctx, rgb)
    ctx.runtime.review_image.base_rgb = _np.ascontiguousarray(rgb)
    state.review_image.base_pil = (
        _PILImage.fromarray(rgb, mode="RGB") if _PIL_AVAILABLE else None
    )
    if not preserve_view:
        state.review_image.zoom = 1.0
        state.review_image.pan_x = 0.0
        state.review_image.pan_y = 0.0
    render_review_image_display(ctx)
    label = ctx.runtime.review_image.label
    if label is not None:
        label._mask_arr = center
        label._raw_arr = arr
    apply_review_image_cursor(ctx)
    suffix = f"  ·  highlighted nucleus {sel_nid}" if sel_nid is not None else ""
    status = ctx.widgets.get_or_none("_review_image_status")
    if status is not None:
        if threshold_unmeasured:
            text = (
                "Binary mask UNAVAILABLE: the per-cell threshold for "
                f"{active_channel_label(ctx)} could not be evaluated for this "
                "field, so the plain image is shown instead — an all-black "
                "frame here would have meant no cell passed, which is not what "
                f"was measured.{suffix}"
            )
        elif binary_mask_mode:
            text = (
                f"Binary mask: cells above threshold for {active_channel_label(ctx)} shown white.{suffix}"
            )
        else:
            text = (
                f"Showing channel {state.channel.active_image_channel.upper()} with included cell boundaries.{suffix}"
            )
        text = f"{text}{_scan_shortfall_suffix(ctx)}"
        legend = _exclusion_legend_html(
            ctx, exclusion_reasons,
            on=bool(getattr(state.review_image, "show_exclusion_layers", False)))
        if legend:
            # Rich text only when there is a legend to draw, and the plain part
            # is escaped because Qt switches the whole label to HTML the moment
            # one tag appears in it.
            status.setText(f"{_html.escape(text)}<br>{legend}")
        else:
            status.setText(text)
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] draw_review_image complete status_channel=%r zoom=%.3f pan=(%.1f, %.1f)",
            state.channel.active_image_channel,
            float(state.review_image.zoom),
            float(state.review_image.pan_x),
            float(state.review_image.pan_y),
        )


def _crop_applies_to_displayed_frame(ctx) -> bool:
    """Whether the Crop scope names the frame currently on the canvas.

    The crop's scope is a property of the dataset, not of the view, so the box
    can be armed for wells and timepoints the canvas is not showing. Drawing the
    same solid outline either way would state that these cells are being
    filtered when they are not — see the outline's own dashed branch.

    The answer is the crop layer's own ``crop_covers_frame``, which judges the
    frame from the scope and the well's rows — the same inputs the ``Crop_Pass``
    verdict uses. Comparing the tokens here instead was wrong in both
    directions: a scope holding a raw ``T24`` read as out-of-scope while cells
    were being excluded, and a frame whose CSV has no timepoint column read as
    in-scope while no row of it could match.
    """
    from review.core.frame_scope import current_frame
    from review.features.crop.controller import crop_covers_frame
    well, fov, tp = current_frame(ctx)
    return crop_covers_frame(ctx, well, fov, tp)


def _draw_crop_outline(ctx, rgb) -> None:
    """Draw the active crop rectangle as an outline on ``rgb``.

    The region is in mask-pixel (row, col) space — the same coordinate system as
    ``rgb`` for the displayed FOV — so it maps directly. No-op when no crop is
    set. A distinct cyan edge keeps it readable over both the red boundaries and
    the white binary mask.

    When the Crop scope does not name the displayed frame the same rectangle is
    drawn dashed and dimmed: the crop exists and is worth showing where it sits,
    but nothing in this frame is excluded by it, and a solid box saying
    otherwise is the kind of silent substitution §7 of CLAUDE.md is about.
    """
    region = ctx.state.dataset.crop_region
    if not region:
        return
    in_scope = _crop_applies_to_displayed_frame(ctx)
    h, w = rgb.shape[:2]
    r0, c0, r1, c1 = region
    r0 = max(0, min(h - 1, int(r0)))
    r1 = max(0, min(h - 1, int(r1)))
    c0 = max(0, min(w - 1, int(c0)))
    c1 = max(0, min(w - 1, int(c1)))
    if r1 <= r0 or c1 <= c0:
        return
    color = (_np.array([0, 220, 255], dtype=_np.uint8) if in_scope
             else _np.array([0, 110, 128], dtype=_np.uint8))
    # Draw the border as bands scaled to the image size (~0.5% of the smaller
    # dimension, min 2 px) so it survives the downscale when zoomed out — a
    # single-pixel edge subsamples away and the crop looks like it vanished.
    t = max(2, int(round(min(h, w) / 200)))
    # Out of scope, the same band is broken into dashes. Dimming alone reads as
    # a rendering artefact at low zoom; a dashed edge does not, and the two
    # together survive both the downscale and a bright background. The dash is
    # long enough (8×) that it never subsamples into a solid line.
    dash = 0 if in_scope else max(2 * t, int(round(min(h, w) / 25)))

    def _band(rl, rh, cl, ch) -> None:
        rl, rh = max(0, rl), min(h, rh)
        cl, ch = max(0, cl), min(w, ch)
        if rh <= rl or ch <= cl:
            return
        if not dash:
            rgb[rl:rh, cl:ch] = color
            return
        # Step along the band's long axis, painting one dash in every two.
        if (ch - cl) >= (rh - rl):
            for start in range(cl, ch, 2 * dash):
                rgb[rl:rh, start:min(ch, start + dash)] = color
        else:
            for start in range(rl, rh, 2 * dash):
                rgb[min(rh, start):min(rh, start + dash), cl:ch] = color
    _band(r0, r0 + t, c0, c1 + 1)            # top
    _band(r1 - t + 1, r1 + 1, c0, c1 + 1)    # bottom
    _band(r0, r1 + 1, c0, c0 + t)            # left
    _band(r0, r1 + 1, c1 - t + 1, c1 + 1)    # right


def render_review_image_display(ctx, *, pan_only: bool = False) -> None:
    # A8 E4-review: ctx-native pixmap-service leaf. The QLabel host, QScrollArea
    # canvas, decoded RGB buffer and cached scale are live objects read via
    # ``ctx.runtime.review_image``; view geometry (zoom/pan/base_pil) is typed
    # state on ``ctx.state.review_image``.
    rt = ctx.runtime.review_image
    label = rt.label
    if label is None:
        return
    ri = ctx.state.review_image
    rgb = rt.base_rgb
    if rgb is None and ri.base_pil is None:
        return
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] render_review_image_display start pan_only=%s",
            pan_only,
        )
    if rgb is not None:
        ih, iw = rgb.shape[:2]
    else:
        iw, ih = ri.base_pil.size
    canvas = rt.canvas
    vp = canvas.viewport()
    cw = max(1, vp.width())
    ch = max(1, vp.height())
    fit = min(cw / max(iw, 1), ch / max(ih, 1))
    scale = max(0.05, fit * max(0.1, float(ri.zoom)))
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))

    cached_scale = rt.scale
    existing_pm = label.pixmap()
    rebuild = not pan_only or existing_pm is None or existing_pm.isNull() or cached_scale != scale

    if rebuild:
        if rgb is not None:
            buf = rgb if rgb.flags["C_CONTIGUOUS"] else _np.ascontiguousarray(rgb)
            qimg = QImage(
                buf.data, iw, ih, 3 * iw, QImage.Format_RGB888,
            ).copy()
            pm = QPixmap.fromImage(qimg)
            if (nw, nh) != (iw, ih):
                pm = pm.scaled(
                    nw, nh, Qt.IgnoreAspectRatio, Qt.FastTransformation,
                )
        else:
            img = ri.base_pil
            shown = img.resize((nw, nh), _PILImage.NEAREST)
            if shown.mode != "RGBA":
                shown = shown.convert("RGBA")
            data = shown.tobytes("raw", "RGBA")
            qimg = QImage(
                data, nw, nh, 4 * nw, QImage.Format_RGBA8888,
            ).copy()
            pm = QPixmap.fromImage(qimg)
        label.setPixmap(pm)
        label.resize(max(nw, cw), max(nh, ch))
        rt.scale = scale
    pan_x = float(ri.pan_x)
    pan_y = float(ri.pan_y)
    hbar = canvas.horizontalScrollBar()
    vbar = canvas.verticalScrollBar()
    cx = max(0, (max(nw, cw) - cw) // 2) - int(pan_x)
    cy = max(0, (max(nh, ch) - ch) // 2) - int(pan_y)
    hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), cx)))
    vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), cy)))
    # Force a synchronous repaint when the pixmap actually changed. setPixmap
    # only schedules an async update(); toggles like Binary/Outline live inside
    # the "View ▾" QMenu popover, whose nested event loop swallows that deferred
    # paint until the menu closes (previously the change showed only after a
    # tab switch forced a redraw). Gated on ``rebuild`` so pan-only reuse — which
    # already repaints via the scrollbar move — stays free of the extra paint.
    if rebuild:
        vp = canvas.viewport()
        if vp is not None:
            vp.repaint()
    refresh_review_image_zoom_label(ctx)
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] render_review_image_display done img=%sx%s shown=%sx%s scale=%.4f rebuild=%s",
            iw, ih, nw, nh, scale, rebuild,
        )


def refresh_review_image_zoom_label(ctx) -> None:
    """Sync the Segmentation control row's zoom readout with the view state.

    Called from the one place every zoom path funnels through — the display
    render — so the buttons, the scroll wheel, and the jump-to-nucleus zoom all
    keep the readout honest without each having to remember to update it. The
    label is absent until the Segmentation panel builds, and in profiles that
    ship no Segmentation tab at all, hence the ``get_or_none``.
    """
    lbl = ctx.widgets.get_or_none("_review_image_zoom_lbl")
    if lbl is None:
        return
    lbl.setText(f"{float(ctx.state.review_image.zoom) * 100:.0f}%")


def review_image_resolve_lut(ctx, arr) -> tuple[float, float]:
    # A8 D4: ctx-native — active channel + the per-channel LUT cache read off
    # typed ``ctx.state``; the LUT min/max boxes are the (unregistered) Qt
    # widgets reached through ``ctx.window``.
    chan = str(ctx.state.channel.active_image_channel or "").lower()
    lmin_entry = getattr(ctx.window, "_review_lmin_entry", None)
    lmax_entry = getattr(ctx.window, "_review_lmax_entry", None)
    if lmin_entry is not None and lmax_entry is not None:
        # Parse each box independently so editing just one (leaving the other
        # at "auto"/blank) still takes effect — the missing side falls back to
        # the data min/max rather than discarding the typed value entirely.
        def _num(text, fallback):
            text = (text or "").strip().lower()
            if not text or text == "auto":
                return fallback
            try:
                return float(text)
            except ValueError:
                return fallback

        lo = _num(lmin_entry.text(), float(arr.min()))
        hi = _num(lmax_entry.text(), float(arr.max()))
        if hi > lo:
            ctx.state.image_view.lut_by_channel[chan] = (lo, hi)
            return lo, hi
    saved = ctx.state.image_view.lut_by_channel.get(chan)
    if saved is not None and saved[1] > saved[0]:
        return saved
    lo = float(arr.min())
    hi = float(arr.max())
    if hi <= lo:
        hi = lo + 1.0
    ctx.state.image_view.lut_by_channel[chan] = (lo, hi)
    return lo, hi



# Default swatch colours for the Review Image overlays (boundary / selected /
# tint). Was ``ReviewApp._REVIEW_IMAGE_COLOR_DEFAULTS`` — moved here so the
# ctx-native colour helpers own it without naming the god-object.
_REVIEW_IMAGE_COLOR_DEFAULTS = {
    "boundary": (255, 64, 64),
    "selected": (255, 230, 64),
    "tint": (255, 255, 255),
}


def review_image_get_color(ctx, which: str) -> tuple[int, int, int]:
    """The current RGB for a Review Image overlay slot (boundary/selected/tint).

    Stored as ``ctx.runtime.review_image.<which>_color`` scratch; falls back to
    the module default when unset."""
    return tuple(
        getattr(ctx.runtime.review_image, f"{which}_color", None)
        or _REVIEW_IMAGE_COLOR_DEFAULTS[which]
    )


def review_image_zoom_fit(ctx) -> None:
    """Reset the Review Image canvas to 1× / no pan and re-render."""
    ctx.state.review_image.zoom = 1.0
    ctx.state.review_image.pan_x = 0.0
    ctx.state.review_image.pan_y = 0.0
    render_review_image_display(ctx)


def toggle_selected_review_cell(ctx) -> None:
    """Flip include-edit (delete) mode — the Delete Cell toolbar toggle."""
    set_review_image_include_mode(
        ctx, not ctx.state.review_image.include_edit_mode
    )


def refresh_review_image_source_btn(ctx) -> None:
    """Sync the Raw / Flat-field / Corrected segmented control with state."""
    seg = ctx.runtime.review_image.source_seg
    if seg is None:
        return
    blocked = seg.blockSignals(True)
    try:
        seg.setCurrentByData(ctx.state.review_image.image_source)
    finally:
        seg.blockSignals(blocked)



def apply_review_image_cursor(ctx) -> None:
    """Set the Review Image cursor based on include-edit mode.

    ForbiddenCursor (the ⊘ 'no entry' icon) indicates remove-cell mode;
    PointingHandCursor indicates normal select/highlight mode. Applied
    to both the label and the scroll viewport so it does not flicker
    back to the arrow over scrollbar gutters or padding.

    A8 D4: ctx-native — edit-mode flags read off typed ``ctx.state``; the QLabel
    host + QScrollArea canvas are the ``ctx.runtime.review_image`` scratch.
    """
    enabled = bool(
        ctx.state.review_image.include_edit_mode
        or ctx.state.review_image.crop_edit_mode
    )
    cursor = Qt.CrossCursor if enabled else Qt.PointingHandCursor
    label = ctx.runtime.review_image.label
    if label is not None:
        label.setCursor(cursor)
    canvas = ctx.runtime.review_image.canvas
    if canvas is not None:
        canvas.setCursor(cursor)
        vp = canvas.viewport()
        if vp is not None:
            vp.setCursor(cursor)



def refresh_review_image_binary_btn(ctx) -> None:
    btn = ctx.runtime.review_image.binary_btn
    if btn is None:
        return
    on = bool(ctx.state.review_image.binary_mask)
    btn.setChecked(on)
    btn.setText("Binary: On" if on else "Binary: Off")
    btn.setToolTip(
        "Binary mask: cells above the active threshold are drawn white,\n"
        "cells below are drawn black, on a black background.\n"
        "Uses the same gating as the bar plots, filtered to the\n"
        "displayed FOV/timepoint."
    )
    btn.style().unpolish(btn)
    btn.style().polish(btn)



def review_image_auto_lut(ctx) -> None:
    arr = ctx.state.review_image.last_fluor_arr
    if arr is None or not _NP_AVAILABLE:
        return
    arr_np = _np.asarray(arr, dtype=_np.float32)
    lo = float(arr_np.min())
    hi = float(arr_np.max())
    if hi <= lo:
        hi = lo + 1.0
    ctx.state.image_view.lut_by_channel[str(ctx.state.channel.active_image_channel or "").lower()] = (lo, hi)
    lmin = ctx.runtime.review.lmin_entry
    lmax = ctx.runtime.review.lmax_entry
    if lmin is not None and lmax is not None:
        lmin.setText(f"{lo:.0f}")
        lmax.setText(f"{hi:.0f}")
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def review_image_refresh_color_swatch(ctx, which: str) -> None:
    swatches = ctx.runtime.review_image.color_swatches or {}
    btn = swatches.get(which)
    if btn is None:
        return
    r, g, b = review_image_get_color(ctx, which)
    # Explicit border-radius on every side so the bottom corners stay
    # rounded — the global QSS rule was being clipped by the swatch's
    # tight fixed size, leaving square lower corners.
    btn.setStyleSheet(
        f"QPushButton {{ background-color: rgb({r},{g},{b}); "
        f"border: 1px solid {theme.Colors.border_strong}; border-radius: 4px; "
        f"min-width: 28px; min-height: 18px; padding: 0; }}"
    )



def set_review_image_source(ctx, mode: str) -> None:
    """Set which image stage the Review Image canvas shows.

    ``mode`` is one of ``"raw"`` (unprocessed fluorescence), ``"flatfield"``
    (raw with only the selected flat-field applied — recomputed on the fly to
    validate the flat-field step before any background subtraction), or
    ``"corrected"`` (the saved final = flat-field + background/top-hat image,
    the default).
    """
    if mode not in ("raw", "flatfield", "corrected"):
        return
    ctx.state.review_image.image_source = mode
    # LUT ranges differ between stages, so drop the active channel's cached LUT
    # so the new stage gets a fresh auto-fit instead of a stale clip.
    chan = str(ctx.state.channel.active_image_channel or "").lower()
    ctx.state.image_view.lut_by_channel.pop(chan, None)
    refresh_review_image_source_btn(ctx)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def set_review_image_include_mode(ctx, enabled: bool) -> None:
    enabled = bool(enabled)
    ctx.state.review_image.include_edit_mode = enabled
    if enabled and ctx.state.review_image.crop_edit_mode:
        # Mutually exclusive with crop-draw mode so an armed rubber band is
        # unambiguous on release. Clear it directly (avoids importing the crop
        # controller, which imports this module).
        ctx.state.review_image.crop_edit_mode = False
        crop_btn = ctx.runtime.review_image.crop_btn
        if crop_btn is not None:
            crop_btn.setChecked(False)
    apply_review_image_cursor(ctx)
    btn = ctx.runtime.review_image.delete_btn
    if btn is not None:
        btn.setChecked(enabled)
    if enabled:
        ctx.notify.set_status(
            "Delete mode ON — click a cell to exclude it, or drag a rectangle to bulk-exclude."
        )
    else:
        ctx.notify.set_status("Delete mode OFF.")



def review_image_commit_lut(ctx) -> None:
    arr = ctx.state.review_image.last_fluor_arr
    if arr is None:
        return
    lo, hi = review_image_resolve_lut(ctx, _np.asarray(arr, dtype=_np.float32))
    lmin = ctx.runtime.review.lmin_entry
    lmax = ctx.runtime.review.lmax_entry
    if lmin is not None and lmax is not None:
        lmin.setText(f"{lo:.0f}")
        lmax.setText(f"{hi:.0f}")
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def refresh_review_image_outline_btn(ctx) -> None:
    btn = ctx.runtime.review_image.outline_btn
    if btn is None:
        return
    on = bool(ctx.state.review_image.show_outline)
    btn.setChecked(on)
    btn.setText("Outline: On" if on else "Outline: Off")
    btn.style().unpolish(btn)
    btn.style().polish(btn)



def invalidate_review_image_frame_cache(ctx) -> None:
    """Drop the decoded fluor / mask / boundary cache.

    Call on data load (well paths change) and on dataset re-scan so the
    next refresh re-decodes from disk.
    """
    ctx.state.review_image.frame_cache = None
    ctx.state.review_image.include_cache.clear()
    ctx.state.review_image.override_version += 1



def toggle_review_image_binary_mask(ctx) -> None:
    """Flip the binary-mask overlay on/off and refresh the canvas."""
    ctx.state.review_image.binary_mask = not bool(
        ctx.state.review_image.binary_mask
    )
    refresh_review_image_binary_btn(ctx)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



#: Ink for each exclusion layer, keyed by the label
#: ``collect.EXCLUSION_LAYERS`` gives it. Design tokens, never literals
#: (ARCHITECTURE §8).
#:
#: **Each answer needs its own colour, and the first draft had five for six.** "Outside the
#: crop" and "outside the scatter gates" were both ``text_muted`` — the same
#: pixel value, CIE76 ΔE of exactly 0 — so the overlay could not distinguish
#: the two layers its own button promises to distinguish. ``text_muted`` is
#: also the darkest ink in the set (relative luminance 0.144), painted as a
#: one-pixel stroke on a dark fluorescence frame, so the two layers that shared
#: a colour were also the two hardest to see.
#:
#: These six were chosen by measuring rather than by eye: every pairwise CIE76
#: ΔE is at least **31**, and every relative luminance at least **0.28**, which
#: is what a single-pixel outline needs against this app's plot surface. The
#: numbers are asserted by ``tests/test_exclusion_overlay.py`` so a later
#: palette edit cannot quietly collapse two answers back into one.
#:
#: Two tokens are deliberately absent. ``accent2`` is reserved by the plate's
#: colour vocabulary and forbidden for anomaly use, and this overlay borrows
#: that rule rather than inventing a second one. ``success`` is the app's own
#: "good" token, and every cell this overlay marks has been *removed* — green
#: for a failure state reads as the opposite of what happened, and on a
#: green-tinted channel it is close to invisible besides.
#:
#: ``text_secondary`` and ``heatmap_selection`` are borrowed from elsewhere in
#: the palette, which is stated here so the borrowing is visible: the first is
#: a neutral grey for the one verdict neither the user nor a threshold on this
#: panel made, and the second is the most separated remaining hue.
#:
#: The seventh, a field excluded by hand in QC triage, is ``ink_light`` (white),
#: and it was chosen the same way: of every theme token not already used and
#: not reserved, it is the only one whose ΔE to all six others clears the 31
#: floor (35.1; the next best, ``accent_fg``, is 30.1). Its luminance is 1.0.
def _layer_inks():
    from ui import theme
    return {
        "deleted here": theme.Colors.danger,
        "debris gate": theme.Colors.warn,
        "outside the crop": theme.Colors.stale,
        "outside the scatter gates": theme.Colors.accent,
        "field flagged by the run": theme.Colors.text_secondary,
        "out of focus": theme.Colors.heatmap_selection,
        "field excluded in QC triage": theme.Colors.ink_light,
    }


def _exclusion_legend_html(ctx, exclusion_reasons, *, on: bool) -> str:
    """The colour key for the overlay, or what it found when there is none.

    **The overlay shipped with no legend anywhere.** The button's tooltip named
    the six layers in prose and never said which colour was which, so the
    picture used six inks the user had no way to read. The Home plate, one
    feature over, refuses to draw its marker without a legend row for exactly
    this reason; this borrows that invariant rather than inventing a second.

    Only the layers **present in this field** are listed, so the key is short
    and every entry answers a cell the user can actually see. And when the
    overlay is on with nothing excluded, that is said out loud: the picture is
    simply unchanged, which is indistinguishable from a toggle that does not
    work (§0.5 — degraded output must be visible in the artifact).
    """
    if not on:
        return ""
    present: list[str] = []
    for layers in (exclusion_reasons or {}).values():
        if layers and layers[0] not in present:
            present.append(layers[0])
    if not present:
        return (f'<span style="color:{theme.Colors.text_muted}">'
                "Why removed: nothing in this field is excluded.</span>")
    inks = _layer_inks()
    order = [label for _col, label in EXCLUSION_LAYERS if label in present]
    keys = " &nbsp; ".join(
        f'<span style="color:{inks[label]}">\u25a0</span> {_html.escape(label)}'
        for label in order)
    return (f'<span style="color:{theme.Colors.text_muted}">Why removed:</span> '
            f"{keys}")


def _paint_exclusion_outlines(rgb, boundary, center, exclusion_reasons, *,
                              on: bool) -> None:
    """Outline each excluded cell in the ink of the layer that removed it.

    Shared by both draw modes so the toggle cannot mean one thing in the
    intensity view and nothing at all in the binary one. A no-op when the
    overlay is off or nothing is excluded; the *caller* is what says so on
    screen, because a picture that is simply unchanged cannot.
    """
    if not on or not exclusion_reasons:
        return
    inks = _layer_inks()
    for nid, layers in exclusion_reasons.items():
        if not layers:
            continue
        # The FIRST layer names the outline, and the hover readout names them
        # all: an outline can only be one colour, and manual comes first in
        # that roster because it is the one a click undoes.
        ink = inks.get(layers[0])
        if ink is None:
            continue
        rgb[boundary & (center == int(nid))] = _np.asarray(
            _rgb_of(ink), dtype=_np.uint8)


def _rgb_of(hex_colour) -> tuple:
    h = str(hex_colour).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def refresh_review_image_layers_btn(ctx) -> None:
    btn = ctx.runtime.review_image.layers_btn
    if btn is None:
        return
    on = bool(ctx.state.review_image.show_exclusion_layers)
    btn.setChecked(on)
    btn.setText("Why removed: On" if on else "Why removed: Off")
    btn.setToolTip(
        "Colour each excluded cell by the layer that removed it — you\n"
        "deleted it, the debris gate, the crop, the scatter gates, the\n"
        "run's field verdict, or the focus cut. The colour key appears\n"
        "under the picture, listing only the layers this field actually\n"
        "has cells for.\n"
        "\n"
        "Inclusion is the AND of all six, so a cell can fail more than one.\n"
        "An outline can only be one colour, so it shows the first — hover a\n"
        "cell to read every layer that dropped it. That also means clicking\n"
        "a cell back in CANNOT restore one a verdict column removed —\n"
        "raise the threshold, or turn that layer off, instead.\n"
        "\n"
        "Works in Binary mask mode too."
    )


def toggle_review_image_exclusion_layers(ctx) -> None:
    """Flip the why-was-this-removed colouring on/off and refresh."""
    ctx.state.review_image.show_exclusion_layers = not bool(
        ctx.state.review_image.show_exclusion_layers
    )
    refresh_review_image_layers_btn(ctx)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)


def toggle_review_image_outline(ctx) -> None:
    """Flip the cell-outline overlay on/off and refresh the canvas."""
    ctx.state.review_image.show_outline = not bool(
        ctx.state.review_image.show_outline
    )
    refresh_review_image_outline_btn(ctx)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def review_image_set_color(ctx, which: str, rgb: tuple[int, int, int]) -> None:
    rgb_clamped = tuple(max(0, min(255, int(c))) for c in rgb)
    setattr(ctx.runtime.review_image, f"{which}_color", rgb_clamped)
    review_image_refresh_color_swatch(ctx, which)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def reset_review_image_colors(ctx) -> None:
    """Restore the default colors for boundary / selection / tint."""
    for which, default in _REVIEW_IMAGE_COLOR_DEFAULTS.items():
        setattr(ctx.runtime.review_image, f"{which}_color", tuple(default))
        review_image_refresh_color_swatch(ctx, which)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def zoom_review_image_to_selected_nucleus_ctx(ctx, zoom: float = 3.0) -> None:
    """ctx-native entry point (kept for the cell-browser caller)."""
    zoom_review_image_to_selected_nucleus(ctx, zoom)


def zoom_review_image_to_selected_nucleus(ctx, zoom: float = 3.0) -> None:
    # A8 E4-review: ctx-native pixmap-service leaf. The QLabel host + QScrollArea
    # canvas + decoded RGB buffer are live objects on ``ctx.runtime.review_image``;
    # zoom/pan/base_pil are typed state on ``ctx.state.review_image``.
    rt = ctx.runtime.review_image
    label = rt.label
    canvas = rt.canvas
    if label is None or canvas is None:
        return
    ri = ctx.state.review_image
    nid = ri.selected_nucleus
    mask_arr = getattr(label, "_mask_arr", None)
    if nid is None or mask_arr is None:
        return
    ys, xs = _np.where(mask_arr == int(nid))
    if len(xs) == 0 or len(ys) == 0:
        return
    cx = float(xs.mean())
    cy = float(ys.mean())
    ri.zoom = float(max(1.0, zoom))
    rgb = rt.base_rgb
    if rgb is not None:
        ih, iw = rgb.shape[:2]
    else:
        img = ri.base_pil
        if img is None:
            return
        iw, ih = img.size
    vp = canvas.viewport()
    cw = max(1, vp.width())
    ch = max(1, vp.height())
    fit = min(cw / max(iw, 1), ch / max(ih, 1))
    scale = max(0.05, fit * max(0.1, float(ri.zoom)))
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    # The renderer derives the scrollbar value as
    #     scroll = max(0, (max(nw, cw) - cw) // 2) - int(pan)
    # and the QLabel host's pixmap sits at x=0 inside it when the
    # scaled image exceeds the viewport (nw > cw), so the centring
    # formula has to be pan = nw/2 - cx*scale (and likewise for y).
    # The old `cw/2 - base_x - cx*scale` form mis-offset the pan by
    # roughly (nw - cw)/2 px once you zoomed in, which is why the
    # yellow nucleus ended up off-screen.
    ri.pan_x = (nw / 2.0) - (cx * scale)
    ri.pan_y = (nh / 2.0) - (cy * scale)
    render_review_image_display(ctx)


def review_image_zoom_step(ctx, direction: int) -> None:
    steps = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
    cur = ctx.state.review_image.zoom
    idx = min(range(len(steps)), key=lambda i: abs(steps[i] - cur))
    idx = max(0, min(len(steps) - 1, idx + direction))
    ctx.state.review_image.zoom = steps[idx]
    render_review_image_display(ctx)

