"""Review-image preview pipeline (image load → render → display).

Extracted from ``WellViewerApp`` so the GUI class no longer owns image
decoding, boundary computation, or pixmap composition. Each function takes
``app`` (WellViewerApp) for state access.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from well_viewer.core import debug_flags as _debug_flags
from well_viewer.data.data_loading import extract_well_token as _extract_well_token
# collect.py is the (Qt-free) data half of this feature and never imports the
# renderer, so these resolve at module scope — no deferred cycle-break needed.
from well_viewer.features.image_browser.review_image.collect import (
    flatfield_field_for,
    review_build_include_map,
    review_build_threshold_map,
    review_collect_timepoints,
    review_norm_fov,
    review_resolve_image_refs,
)

_logger = logging.getLogger("well_viewer")

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


def update_preview(app, well_label: Optional[str]) -> None:
    """Legacy app-typed entry: bridge to the ctx-native preview pipeline.

    Kept so the still-app callers (registry ``_activate_segmentation``,
    ``preview_view``, ``channel_state_controller``) call it unchanged.
    """
    update_preview_ctx(app._ctx, well_label)


def update_preview_ctx(ctx, well_label: Optional[str]) -> None:
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


def _update_preview_impl(ctx, well_label: Optional[str]) -> None:
    # Local imports keep this module standalone-importable without paying
    # the cost of dragging in runtime_app at module load time.
    from well_viewer.data.image_discovery import find_well_images_and_masks
    from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values

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
        fluor, overlay, mask, tophat_fluor = find_well_images_and_masks(
            state.dataset.data_dir,
            well_label,
            fluor_token=active_image_channel,
            in_dir=state.dataset.in_dir,
            _fov_tp_extractor=state.dataset.fov_tp_extractor,
            _pipeline_info=state.dataset.pipeline_info,
        )
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

    def _norm_fov(value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            return f"{float(raw):g}"
        except Exception:
            return raw

    def _fov_sort_key(token: str) -> tuple[int, float, str]:
        try:
            return (0, float(token), token)
        except ValueError:
            return (1, 0.0, token)

    all_fovs = sorted(
        {
            fov_norm
            for refs in (fluor, overlay, mask, tophat_fluor)
            for (fov, _tp) in refs.keys()
            for fov_norm in [_norm_fov(fov)]
            if fov_norm
        },
        key=_fov_sort_key,
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
    from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values

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


def refresh_review_image(app) -> None:
    """App-native compatibility entry — bridge to the ctx-native refresh.

    Kept so the many still-app callers (renderer toggles, controller cell
    edits, crop / registry / preview-panel / channel-switch handlers) invoke it
    unchanged; the body reaches the context via ``app._ctx`` (the app→ctx
    direction), never the ``app``-escape-hatch field on the context."""
    refresh_review_image_ctx(app._ctx)


def _resolve_review_fov_tp(ctx, widgets, status, _set_combo_values, *, well, channel_switch_debug: bool):
    """Resolve + validate the active (fov_raw, fov, tp_raw, tp) for the redraw.

    Drives the FOV/TP combos, normalises both tokens, and writes the abort
    status line + debug log on an invalid selection. Returns the 4-tuple, or
    ``None`` when the FOV or timepoint is missing (caller then aborts the draw).
    Split out of ``_refresh_review_image_impl`` to keep that orchestrator's
    cyclomatic complexity in check (A8 D4)."""
    from well_viewer.core.ui_helpers import norm_timepoint as _norm_timepoint

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
    from well_viewer.data.image_discovery import open_imgref_as_array
    from well_viewer.core.channel_state_controller import (
        get_all_fluor_gates, get_thresh_frac_on,
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
        fluor_raw = open_imgref_as_array(fluor_ref, greyscale=True)
        mask_raw = open_imgref_as_array(mask_ref, greyscale=True)
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
                tp=tp)
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

    above_by_nid: Optional[Dict[int, bool]] = None
    if bool(state.review_image.binary_mask):
        fluor_gates = get_all_fluor_gates(ctx)
        threshold = get_thresh_frac_on(ctx)
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
        above_by_nid=above_by_nid,
    )


def draw_review_image(
    ctx,
    fluor_arr,
    mask_arr,
    include_by_nid: Dict[int, bool],
    *,
    fit_lut: bool = False,
    preserve_view: bool = False,
    boundary=None,
    above_by_nid: Optional[Dict[int, bool]] = None,
) -> None:
    # A8 D4: ctx-native pixmap compositor. Typed view/colour state lives on
    # ``ctx.state.review_image`` / ``ctx.state.image_view``; the QLabel host +
    # decoded RGB buffer are the ``ctx.runtime.review_image`` scratch; the LUT
    # boxes / status label are Qt widgets reached through ``ctx.widgets`` (the
    # registered status label) or ``ctx.window`` (the unregistered LUT entries).
    from well_viewer.core.channel_state_controller import active_channel_label
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

    binary_mask_mode = bool(state.review_image.binary_mask)
    sel_nid = state.review_image.selected_nucleus

    if binary_mask_mode:
        if above_by_nid is None:
            above_by_nid = {}
        rgb = _np.zeros((h, w, 3), dtype=_np.uint8)
        above_ids = [int(nid) for nid, ab in above_by_nid.items() if ab]
        if above_ids:
            above_arr = _np.fromiter(above_ids, dtype=center.dtype, count=len(above_ids))
            above_mask = _np.isin(center, above_arr)
            rgb[above_mask] = _np.array([255, 255, 255], dtype=_np.uint8)
        if sel_nid is not None:
            sel_color = _np.asarray(state.review_image.selected_color, dtype=_np.uint8)
            sel_boundary = boundary & (center == int(sel_nid))
            rgb[sel_boundary] = sel_color
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
            if sel_nid is not None:
                sel_color = _np.asarray(state.review_image.selected_color, dtype=_np.uint8)
                sel_boundary = boundary & (center == int(sel_nid))
                rgb[sel_boundary] = sel_color
        elif sel_nid is not None:
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
        if binary_mask_mode:
            status.setText(
                f"Binary mask: cells above threshold for {active_channel_label(ctx)} shown white.{suffix}"
            )
        else:
            status.setText(
                f"Showing channel {state.channel.active_image_channel.upper()} with included cell boundaries.{suffix}"
            )
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] draw_review_image complete status_channel=%r zoom=%.3f pan=(%.1f, %.1f)",
            state.channel.active_image_channel,
            float(state.review_image.zoom),
            float(state.review_image.pan_x),
            float(state.review_image.pan_y),
        )


def _draw_crop_outline(ctx, rgb) -> None:
    """Draw the active dataset-wide crop rectangle as an outline on ``rgb``.

    The region is in mask-pixel (row, col) space — the same coordinate system as
    ``rgb`` for the displayed FOV — so it maps directly. No-op when no crop is
    set. A distinct cyan edge keeps it readable over both the red boundaries and
    the white binary mask.
    """
    region = ctx.state.dataset.crop_region
    if not region:
        return
    h, w = rgb.shape[:2]
    r0, c0, r1, c1 = region
    r0 = max(0, min(h - 1, int(r0)))
    r1 = max(0, min(h - 1, int(r1)))
    c0 = max(0, min(w - 1, int(c0)))
    c1 = max(0, min(w - 1, int(c1)))
    if r1 <= r0 or c1 <= c0:
        return
    color = _np.array([0, 220, 255], dtype=_np.uint8)
    # Draw the border as bands scaled to the image size (~0.5% of the smaller
    # dimension, min 2 px) so it survives the downscale when zoomed out — a
    # single-pixel edge subsamples away and the crop looks like it vanished.
    t = max(2, int(round(min(h, w) / 200)))

    def _band(rl, rh, cl, ch) -> None:
        rgb[max(0, rl):min(h, rh), max(0, cl):min(w, ch)] = color
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
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] render_review_image_display done img=%sx%s shown=%sx%s scale=%.4f rebuild=%s",
            iw, ih, nw, nh, scale, rebuild,
        )


def review_image_resolve_lut(ctx, arr) -> Tuple[float, float]:
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
# tint). Was ``WellViewerApp._REVIEW_IMAGE_COLOR_DEFAULTS`` — moved here so the
# ctx-native colour helpers own it without naming the god-object.
_REVIEW_IMAGE_COLOR_DEFAULTS = {
    "boundary": (255, 64, 64),
    "selected": (255, 230, 64),
    "tint": (255, 255, 255),
}


def review_image_get_color(ctx, which: str) -> Tuple[int, int, int]:
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
        f"border: 1px solid #444; border-radius: 4px; "
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
        ctx.set_status(
            "Delete mode ON — click a cell to exclude it, or drag a rectangle to bulk-exclude."
        )
    else:
        ctx.set_status("Delete mode OFF.")



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



def toggle_review_image_outline(ctx) -> None:
    """Flip the cell-outline overlay on/off and refresh the canvas."""
    ctx.state.review_image.show_outline = not bool(
        ctx.state.review_image.show_outline
    )
    refresh_review_image_outline_btn(ctx)
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)



def review_image_set_color(ctx, which: str, rgb: Tuple[int, int, int]) -> None:
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



def review_image_zoom_step(ctx, direction: int) -> None:
    steps = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
    cur = ctx.state.review_image.zoom
    idx = min(range(len(steps)), key=lambda i: abs(steps[i] - cur))
    idx = max(0, min(len(steps) - 1, idx + direction))
    ctx.state.review_image.zoom = steps[idx]
    render_review_image_display(ctx)

