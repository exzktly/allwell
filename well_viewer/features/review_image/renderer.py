"""Review-image preview pipeline (image load → render → display).

Extracted from ``WellViewerApp`` so the GUI class no longer owns image
decoding, boundary computation, or pixmap composition. Each function takes
``app`` (WellViewerApp) for state access.
"""

from __future__ import annotations
from well_viewer.features.preview.montage import refresh_preview_montage

import logging
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from well_viewer.core import debug_flags as _debug_flags
from well_viewer.data.data_loading import extract_well_token as _extract_well_token

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
    """Load images for *well_label* and render the inline montage."""
    # Same re-entry guard as refresh_review_image: update_preview drives
    # the FOV combo, whose signal handler calls back into the review-image
    # refresh path, which can re-enter update_preview through the channel
    # / well plate callbacks.
    if getattr(app, "_update_preview_in_progress", False):
        return
    app._update_preview_in_progress = True
    try:
        _update_preview_impl(app, well_label)
    finally:
        app._update_preview_in_progress = False


def _update_preview_impl(app, well_label: Optional[str]) -> None:
    # Local imports keep this module standalone-importable without paying
    # the cost of dragging in runtime_app at module load time.
    from well_viewer.data.image_discovery import find_well_images_and_masks
    from well_viewer.core.ui_helpers import clear_layout as _clear_layout, set_combo_values as _set_combo_values

    channel_switch_debug = _debug_flags.review_image_channel_switch_debug_enabled()
    if well_label is None:
        if hasattr(app, "_preview_well_lbl"):
            app._preview_well_lbl.setText("No well selected")
        if hasattr(app, "_review_image_well_lbl"):
            app._review_image_well_lbl.setText("No well selected")
        if hasattr(app, "_fov_menu"):
            _set_combo_values(app._fov_menu, ["—"])
            app._preview_fov_var.setCurrentText("—")
        if hasattr(app, "_review_image_fov_menu"):
            _set_combo_values(app._review_image_fov_menu, ["—"])
        if hasattr(app, "_review_image_tp_menu"):
            _set_combo_values(app._review_image_tp_menu, ["—"])
            app._review_image_tp_var.setCurrentText("—")
        app._app_state.preview.fluor = app._app_state.preview.overlay = app._app_state.preview.mask = {}
        if hasattr(app, "_montage_inner"):
            _clear_layout(app._montage_inner.layout())
            app._app_state.preview.montage_photos.clear()
            app._montage_status.setText("Select a well in the left panel.")
        if hasattr(app, "_review_image_status"):
            app._review_image_status.setText("Select a well in the left panel.")
        if channel_switch_debug:
            _logger.debug("[RI-CHSW step 4] update_preview early return: no well selected")
        return

    if hasattr(app, "_preview_well_lbl"):
        tok = _extract_well_token(well_label) or well_label
        app._preview_well_lbl.setText(tok)
    if hasattr(app, "_review_image_well_lbl"):
        tok = _extract_well_token(well_label) or well_label
        app._review_image_well_lbl.setText(tok)

    try:
        active_image_channel = str(app._app_state.channel.active_image_channel or "").strip().lower()
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 4] update_preview start well=%r active_channel=%r",
                well_label, active_image_channel,
            )
        fluor, overlay, mask, tophat_fluor = find_well_images_and_masks(
            app._app_state.dataset.data_dir,
            well_label,
            fluor_token=active_image_channel,
            in_dir=app._app_state.dataset.in_dir,
            _fov_tp_extractor=app._app_state.dataset.fov_tp_extractor,
            _pipeline_info=app._app_state.dataset.pipeline_info,
        )
    except Exception as _exc:
        _logger.exception("Unexpected error searching images for %r: %s", well_label, _exc)
        fluor, overlay, mask, tophat_fluor = {}, {}, {}, {}
    app._app_state.preview.fluor = fluor
    app._app_state.preview.overlay = overlay
    app._app_state.preview.mask = mask
    app._preview_tophat_fluor = tophat_fluor
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 4] update_preview refs loaded well=%r active_channel=%r fluor=%d tophat=%d overlay=%d mask=%d",
            well_label, active_image_channel,
            len(fluor), len(tophat_fluor), len(overlay), len(mask),
        )

    if hasattr(app, "_th_checkbox"):
        app._update_tophat_controls(preloaded=False)

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
        _logger.debug(
            "[RI-CHSW step 4] update_preview candidate_fovs=%s selected_fov_before=%r",
            all_fovs,
            app._preview_fov_var.currentText() if hasattr(app, "_preview_fov_var") else "—",
        )

    if not (fluor or overlay or mask or tophat_fluor):
        if hasattr(app, "_fov_menu"):
            _set_combo_values(app._fov_menu, ["—"])
            app._preview_fov_var.setCurrentText("—")
        tok = _extract_well_token(well_label) or well_label
        dirs = f"in={app._app_state.dataset.in_dir}  out={app._app_state.dataset.data_dir}"
        msg = f"No images found for {tok}. Searched: {dirs}"
        _logger.warning(msg)
        if hasattr(app, "_montage_status"):
            app._montage_status.setText(f"No images found for {tok} — check Log for details.")
        return

    if hasattr(app, "_fov_menu"):
        _set_combo_values(app._fov_menu, all_fovs)
        cur = app._preview_fov_var.currentText()
        if all_fovs:
            app._preview_fov_var.setCurrentText(cur if cur in all_fovs else all_fovs[0])
        else:
            app._preview_fov_var.setCurrentText("—")
    if hasattr(app, "_review_image_fov_menu"):
        _set_combo_values(app._review_image_fov_menu, all_fovs or ["—"])

    if hasattr(app, "_preview_fov_var"):
        cur = app._preview_fov_var.currentText()
        if all_fovs and cur not in all_fovs:
            app._preview_fov_var.setCurrentText(all_fovs[0])

    try:
        refresh_preview_montage(app)
    except AttributeError:
        pass
    if channel_switch_debug:
        _logger.debug("[RI-CHSW step 4->6] triggering refresh_review_image after preview reload")
    refresh_review_image(app)


def refresh_review_image(app) -> None:
    """Reload the Review Image canvas for the active well/FOV/timepoint."""
    from well_viewer.data.image_discovery import open_imgref_as_array
    from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values

    if not hasattr(app, "_review_image_label"):
        return
    # Re-entry guard: refresh_review_image programmatically updates the
    # FOV / TP combos, whose currentIndexChanged signal re-invokes this
    # function. Without the guard, a Review-CSV double-click that walks
    # tab/sub-tab/FOV/TP all at once can stack signal callbacks deep
    # enough to trip RecursionError. Skip the nested call -- the outer
    # invocation will redraw with the final state.
    if getattr(app, "_refresh_review_image_in_progress", False):
        return
    app._refresh_review_image_in_progress = True
    try:
        _refresh_review_image_impl(app, _set_combo_values, channel_switch_debug=_debug_flags.review_image_channel_switch_debug_enabled(), image_load_debug=_debug_flags.review_image_load_debug_enabled())
    finally:
        app._refresh_review_image_in_progress = False


def _refresh_review_image_impl(app, _set_combo_values, *, channel_switch_debug: bool, image_load_debug: bool) -> None:
    from well_viewer.data.image_discovery import open_imgref_as_array

    well = app._app_state.preview.selected_well
    if well is None:
        if channel_switch_debug:
            _logger.debug("[RI-CHSW step 6] refresh_review_image aborted: no selected well")
        return

    fov_raw = ""
    if hasattr(app, "_review_image_fov_menu"):
        fov_raw = str(app._review_image_fov_menu.currentText() or "").strip()
    if not fov_raw and hasattr(app, "_preview_fov_var"):
        fov_raw = str(app._preview_fov_var.currentText() or "").strip()
    fov = app._review_norm_fov(fov_raw)
    if not fov_raw or fov_raw == "—" or not fov:
        app._review_image_status.setText("No FOV selected.")
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 6] refresh_review_image aborted: invalid fov raw=%r norm=%r",
                fov_raw, fov,
            )
        return

    tp_values = app._review_collect_timepoints(fov)
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 6] refresh_review_image start well=%r selected_fov_raw=%r normalized_fov=%r active_channel=%r",
            well, fov_raw, fov, app._app_state.channel.active_image_channel,
        )
    _set_combo_values(app._review_image_tp_menu, tp_values or ["—"])
    if tp_values and app._review_image_tp_var.currentText() not in tp_values:
        app._review_image_tp_var.setCurrentText(tp_values[0])
    tp_raw = str(app._review_image_tp_var.currentText() or "").strip()
    tp = app._norm_timepoint(tp_raw)
    if not tp_raw or tp_raw == "—" or not tp:
        app._review_image_status.setText("No timepoint selected.")
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 6] refresh_review_image aborted: invalid timepoint raw=%r norm=%r",
                tp_raw, tp,
            )
        return

    from well_viewer.features.review_image.controller import review_resolve_image_refs
    fluor_ref, mask_ref = review_resolve_image_refs(app, fov_raw=fov_raw, tp_raw=tp_raw)
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
        app._review_image_status.setText(
            "Missing fluorescence image or label map for selected FOV/timepoint."
        )
        if channel_switch_debug:
            _logger.debug(
                "[RI-CHSW step 6] refresh_review_image missing refs fluor_ref=%r mask_ref=%r",
                fluor_ref, mask_ref,
            )
        return
    app._app_state.review_image.is_tif = str(getattr(fluor_ref, "name", "")).lower().endswith((".tif", ".tiff"))
    if not _NP_AVAILABLE:
        app._review_image_status.setText("Could not render review image (numpy unavailable).")
        return

    cache_key = (
        getattr(fluor_ref, "full_path_str", id(fluor_ref)),
        getattr(mask_ref, "full_path_str", id(mask_ref)),
    )
    cached = app._app_state.review_image.frame_cache
    if cached is not None and cached.get("key") == cache_key:
        fluor_arr = cached["fluor_arr"]
        center = cached["center"]
        boundary = cached["boundary"]
    else:
        fluor_raw = open_imgref_as_array(fluor_ref, greyscale=True)
        mask_raw = open_imgref_as_array(mask_ref, greyscale=True)
        if fluor_raw is None or mask_raw is None:
            app._review_image_status.setText("Could not render review image (image decode failed).")
            return
        fluor_arr = _np.asarray(fluor_raw, dtype=_np.float32)
        center_int = _np.rint(_np.asarray(mask_raw)).astype(_np.int32, copy=False)
        padded = _np.pad(center_int, 1, mode="constant", constant_values=0)
        center = padded[1:-1, 1:-1]
        boundary = (center > 0) & (
            (center != padded[:-2, 1:-1]) |
            (center != padded[2:, 1:-1]) |
            (center != padded[1:-1, :-2]) |
            (center != padded[1:-1, 2:])
        )
        app._app_state.review_image.frame_cache = {
            "key": cache_key,
            "fluor_arr": fluor_arr,
            "center": center,
            "boundary": boundary,
        }

    ic_key = (well, fov, tp, app._app_state.review_image.override_version)
    include_by_nid = app._app_state.review_image.include_cache.get(ic_key)
    if include_by_nid is None:
        from well_viewer.features.review_image.controller import review_build_include_map
        include_by_nid = review_build_include_map(app, center, well, fov, tp)
        if len(app._app_state.review_image.include_cache) > 32:
            app._app_state.review_image.include_cache.clear()
        app._app_state.review_image.include_cache[ic_key] = include_by_nid

    above_by_nid: Optional[Dict[int, bool]] = None
    if bool(app._app_state.review_image.binary_mask):
        cell_area_threshold = app._get_cell_area_threshold()
        fluor_gates = app._get_all_fluor_gates()
        threshold = app._get_thresh_frac_on()
        tm_key = (
            well, fov, tp, app._app_state.review_image.override_version,
            app._app_state.channel.active_val_col, threshold, cell_area_threshold,
            tuple(sorted(fluor_gates.items())),
        )
        above_by_nid = app._app_state.review_image.threshold_map_cache.get(tm_key)
        if above_by_nid is None:
            from well_viewer.features.review_image.controller import review_build_threshold_map
            above_by_nid = review_build_threshold_map(app, center, well, fov, tp)
            if len(app._app_state.review_image.threshold_map_cache) > 32:
                app._app_state.review_image.threshold_map_cache.clear()
            app._app_state.review_image.threshold_map_cache[tm_key] = above_by_nid

    preserve_view = bool(app._app_state.review_image.preserve_view_on_refresh)
    app._app_state.review_image.preserve_view_on_refresh = False
    if channel_switch_debug:
        _logger.debug("[RI-CHSW step 6->7] draw_review_image preserve_view=%s", preserve_view)
    draw_review_image(
        app,
        fluor_arr, center, include_by_nid,
        fit_lut=False, preserve_view=preserve_view, boundary=boundary,
        above_by_nid=above_by_nid,
    )


def draw_review_image(
    app,
    fluor_arr,
    mask_arr,
    include_by_nid: Dict[int, bool],
    *,
    fit_lut: bool = False,
    preserve_view: bool = False,
    boundary=None,
    above_by_nid: Optional[Dict[int, bool]] = None,
) -> None:
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] draw_review_image channel=%r fit_lut=%s preserve_view=%s",
            app._app_state.channel.active_image_channel,
            fit_lut, preserve_view,
        )
    arr = _np.asarray(fluor_arr, dtype=_np.float32)
    app._app_state.review_image.last_fluor_arr = arr
    m = _np.asarray(mask_arr)
    if fit_lut:
        lo, hi = float(arr.min()), float(arr.max())
        if hi <= lo:
            hi = lo + 1.0
        app._app_state.review_image.lut_by_channel[str(app._app_state.channel.active_image_channel or "").lower()] = (lo, hi)
    else:
        lo, hi = app._review_image_resolve_lut(arr)
    if hasattr(app, "_review_lut_chan_lbl"):
        app._review_lut_chan_lbl.setText(f"{app._app_state.channel.active_image_channel.upper()} LUT min:")
    if hasattr(app, "_review_lut_min_edit") and hasattr(app, "_review_lut_max_edit"):
        app._review_lut_min_edit.setText(f"{lo:.0f}")
        app._review_lut_max_edit.setText(f"{hi:.0f}")
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

    binary_mask_mode = bool(app._app_state.review_image.binary_mask)
    sel_nid = app._app_state.review_image.selected_nucleus

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
            sel_color = _np.asarray(app._app_state.review_image.selected_color, dtype=_np.uint8)
            sel_boundary = boundary & (center == int(sel_nid))
            rgb[sel_boundary] = sel_color
    else:
        tint = app._app_state.review_image.tint_color
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
        show_outline = bool(app._app_state.review_image.show_outline)
        if show_outline:
            draw_boundary = boundary & include_mask
            boundary_color = _np.asarray(app._app_state.review_image.boundary_color, dtype=_np.uint8)
            rgb[draw_boundary] = boundary_color
            if sel_nid is not None:
                sel_color = _np.asarray(app._app_state.review_image.selected_color, dtype=_np.uint8)
                sel_boundary = boundary & (center == int(sel_nid))
                rgb[sel_boundary] = sel_color
        elif sel_nid is not None:
            sel_color = _np.asarray(app._app_state.review_image.selected_color, dtype=_np.uint8)
            sel_boundary = boundary & (center == int(sel_nid))
            rgb[sel_boundary] = sel_color

    app._review_image_base_rgb = _np.ascontiguousarray(rgb)
    app._app_state.review_image.base_pil = (
        _PILImage.fromarray(rgb, mode="RGB") if _PIL_AVAILABLE else None
    )
    if not preserve_view:
        app._app_state.review_image.zoom = 1.0
        app._app_state.review_image.pan_x = 0.0
        app._app_state.review_image.pan_y = 0.0
    render_review_image_display(app)
    app._review_image_label._mask_arr = center
    app._review_image_label._raw_arr = arr
    app._apply_review_image_cursor()
    suffix = f"  ·  highlighted nucleus {sel_nid}" if sel_nid is not None else ""
    if binary_mask_mode:
        app._review_image_status.setText(
            f"Binary mask: cells above threshold for {app._active_channel_label()} shown white.{suffix}"
        )
    else:
        app._review_image_status.setText(
            f"Showing channel {app._app_state.channel.active_image_channel.upper()} with included cell boundaries.{suffix}"
        )
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] draw_review_image complete status_channel=%r zoom=%.3f pan=(%.1f, %.1f)",
            app._app_state.channel.active_image_channel,
            float(app._app_state.review_image.zoom),
            float(app._app_state.review_image.pan_x),
            float(app._app_state.review_image.pan_y),
        )


def render_review_image_display(app, *, pan_only: bool = False) -> None:
    if not hasattr(app, "_review_image_label"):
        return
    rgb = getattr(app, "_review_image_base_rgb", None)
    if rgb is None and app._app_state.review_image.base_pil is None:
        return
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] render_review_image_display start pan_only=%s",
            pan_only,
        )
    if rgb is not None:
        ih, iw = rgb.shape[:2]
    else:
        iw, ih = app._app_state.review_image.base_pil.size
    vp = app._review_image_canvas.viewport()
    cw = max(1, vp.width())
    ch = max(1, vp.height())
    fit = min(cw / max(iw, 1), ch / max(ih, 1))
    scale = max(0.05, fit * max(0.1, float(app._app_state.review_image.zoom)))
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))

    cached_scale = getattr(app, "_review_image_scale", None)
    existing_pm = app._review_image_label.pixmap()
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
            img = app._app_state.review_image.base_pil
            shown = img.resize((nw, nh), _PILImage.NEAREST)
            if shown.mode != "RGBA":
                shown = shown.convert("RGBA")
            data = shown.tobytes("raw", "RGBA")
            qimg = QImage(
                data, nw, nh, 4 * nw, QImage.Format_RGBA8888,
            ).copy()
            pm = QPixmap.fromImage(qimg)
        app._review_image_label.setPixmap(pm)
        app._review_image_label.resize(max(nw, cw), max(nh, ch))
        app._review_image_scale = scale
    pan_x = float(app._app_state.review_image.pan_x)
    pan_y = float(app._app_state.review_image.pan_y)
    hbar = app._review_image_canvas.horizontalScrollBar()
    vbar = app._review_image_canvas.verticalScrollBar()
    cx = max(0, (max(nw, cw) - cw) // 2) - int(pan_x)
    cy = max(0, (max(nh, ch) - ch) // 2) - int(pan_y)
    hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), cx)))
    vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), cy)))
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 7] render_review_image_display done img=%sx%s shown=%sx%s scale=%.4f rebuild=%s",
            iw, ih, nw, nh, scale, rebuild,
        )


def review_image_resolve_lut(app, arr) -> Tuple[float, float]:
    chan = str(app._app_state.channel.active_image_channel or "").lower()
    if hasattr(app, "_review_lmin_entry") and hasattr(app, "_review_lmax_entry"):
        try:
            lo = float(app._review_lmin_entry.text().strip())
            hi = float(app._review_lmax_entry.text().strip())
            if hi > lo:
                app._app_state.review_image.lut_by_channel[chan] = (lo, hi)
                return lo, hi
        except Exception:
            pass
    saved = app._app_state.review_image.lut_by_channel.get(chan)
    if saved is not None and saved[1] > saved[0]:
        return saved
    lo = float(arr.min())
    hi = float(arr.max())
    if hi <= lo:
        hi = lo + 1.0
    app._app_state.review_image.lut_by_channel[chan] = (lo, hi)
    return lo, hi



def refresh_review_image_source_btn(app) -> None:
    """Sync the raw/top-hat toggle button's caption + variant with state."""
    btn = getattr(app, "_review_image_raw_btn", None)
    if btn is None:
        return
    on = bool(app._app_state.review_image.show_raw)
    btn.setChecked(on)
    btn.setText("Raw" if on else "Top-hat")
    btn.setProperty("variant", "toggle")
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    btn.setToolTip(
        "Showing the unprocessed fluorescence frame.\n"
        "Click to switch back to the top-hat-filtered image."
        if on else
        "Showing the top-hat-filtered fluorescence frame (default).\n"
        "Click to switch to the unprocessed raw image."
    )



def apply_review_image_cursor(app) -> None:
    """Set the Review Image cursor based on include-edit mode.

    ForbiddenCursor (the ⊘ 'no entry' icon) indicates remove-cell mode;
    PointingHandCursor indicates normal select/highlight mode. Applied
    to both the label and the scroll viewport so it does not flicker
    back to the arrow over scrollbar gutters or padding.
    """
    enabled = bool(app._app_state.review_image.include_edit_mode)
    cursor = Qt.CrossCursor if enabled else Qt.PointingHandCursor
    if hasattr(app, "_review_image_label"):
        app._review_image_label.setCursor(cursor)
    canvas = getattr(app, "_review_image_canvas", None)
    if canvas is not None:
        canvas.setCursor(cursor)
        vp = canvas.viewport()
        if vp is not None:
            vp.setCursor(cursor)



def draw_review_image(
    app,
    fluor_arr,
    mask_arr,
    include_by_nid: Dict[int, bool],
    *,
    fit_lut: bool = False,
    preserve_view: bool = False,
    boundary=None,
    above_by_nid: Optional[Dict[int, bool]] = None,
) -> None:
    from well_viewer.features.review_image.renderer import draw_review_image
    draw_review_image(
        app, fluor_arr, mask_arr, include_by_nid,
        fit_lut=fit_lut, preserve_view=preserve_view, boundary=boundary,
        above_by_nid=above_by_nid,
    )



def refresh_review_image_binary_btn(app) -> None:
    btn = getattr(app, "_review_image_binary_btn", None)
    if btn is None:
        return
    on = bool(app._app_state.review_image.binary_mask)
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



def review_image_auto_lut(app) -> None:
    arr = app._app_state.review_image.last_fluor_arr
    if arr is None or not _NP_AVAILABLE:
        return
    arr_np = _np.asarray(arr, dtype=_np.float32)
    lo = float(arr_np.min())
    hi = float(arr_np.max())
    if hi <= lo:
        hi = lo + 1.0
    app._app_state.review_image.lut_by_channel[str(app._app_state.channel.active_image_channel or "").lower()] = (lo, hi)
    if hasattr(app, "_review_lmin_entry") and hasattr(app, "_review_lmax_entry"):
        app._review_lmin_entry.setText(f"{lo:.0f}")
        app._review_lmax_entry.setText(f"{hi:.0f}")
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def review_image_refresh_color_swatch(app, which: str) -> None:
    swatches = getattr(app, "_review_image_color_swatches", {}) or {}
    btn = swatches.get(which)
    if btn is None:
        return
    r, g, b = app._review_image_get_color(which)
    # Explicit border-radius on every side so the bottom corners stay
    # rounded — the global QSS rule was being clipped by the swatch's
    # tight fixed size, leaving square lower corners.
    btn.setStyleSheet(
        f"QPushButton {{ background-color: rgb({r},{g},{b}); "
        f"border: 1px solid #444; border-radius: 4px; "
        f"min-width: 28px; min-height: 18px; padding: 0; }}"
    )



def toggle_review_image_source(app) -> None:
    """Flip raw vs top-hat preference for the Review Image fluorescence channel."""
    app._app_state.review_image.show_raw = not bool(app._app_state.review_image.show_raw)
    # The LUT range for raw vs top-hat differs significantly, so drop the
    # cached LUT for the active channel so the new image gets a fresh
    # auto-fit instead of a stale clip.
    chan = str(app._app_state.channel.active_image_channel or "").lower()
    if chan in app._app_state.review_image.lut_by_channel:
        del app._app_state.review_image.lut_by_channel[chan]
    app._refresh_review_image_source_btn()
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def set_review_image_include_mode(app, enabled: bool) -> None:
    app._app_state.review_image.include_edit_mode = bool(enabled)
    app._apply_review_image_cursor()
    btn = getattr(app, "_review_image_delete_btn", None)
    if btn is not None:
        btn.setChecked(bool(enabled))
    if enabled:
        app._set_status(
            "Delete mode ON — click a cell to exclude it, or drag a rectangle to bulk-exclude."
        )
    else:
        app._set_status("Delete mode OFF.")



def review_image_commit_lut(app) -> None:
    arr = app._app_state.review_image.last_fluor_arr
    if arr is None:
        return
    lo, hi = app._review_image_resolve_lut(_np.asarray(arr, dtype=_np.float32))
    if hasattr(app, "_review_lmin_entry") and hasattr(app, "_review_lmax_entry"):
        app._review_lmin_entry.setText(f"{lo:.0f}")
        app._review_lmax_entry.setText(f"{hi:.0f}")
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def refresh_review_image_outline_btn(app) -> None:
    btn = getattr(app, "_review_image_outline_btn", None)
    if btn is None:
        return
    on = bool(app._app_state.review_image.show_outline)
    btn.setChecked(on)
    btn.setText("Outline: On" if on else "Outline: Off")
    btn.style().unpolish(btn)
    btn.style().polish(btn)



def invalidate_review_image_frame_cache(app) -> None:
    """Drop the decoded fluor / mask / boundary cache.

    Call on data load (well paths change) and on dataset re-scan so the
    next refresh re-decodes from disk.
    """
    app._app_state.review_image.frame_cache = None
    app._app_state.review_image.include_cache.clear()
    app._app_state.review_image.override_version += 1



def toggle_review_image_binary_mask(app) -> None:
    """Flip the binary-mask overlay on/off and refresh the canvas."""
    app._app_state.review_image.binary_mask = not bool(
        app._app_state.review_image.binary_mask
    )
    app._refresh_review_image_binary_btn()
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def toggle_review_image_outline(app) -> None:
    """Flip the cell-outline overlay on/off and refresh the canvas."""
    app._app_state.review_image.show_outline = not bool(
        app._app_state.review_image.show_outline
    )
    app._refresh_review_image_outline_btn()
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def review_image_set_color(app, which: str, rgb: Tuple[int, int, int]) -> None:
    attr = f"_review_image_{which}_color"
    rgb_clamped = tuple(max(0, min(255, int(c))) for c in rgb)
    setattr(app, attr, rgb_clamped)
    app._review_image_refresh_color_swatch(which)
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def reset_review_image_colors(app) -> None:
    """Restore the default colors for boundary / selection / tint."""
    for which, default in app._REVIEW_IMAGE_COLOR_DEFAULTS.items():
        setattr(app, f"_review_image_{which}_color", tuple(default))
        app._review_image_refresh_color_swatch(which)
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)



def review_image_zoom_step(app, direction: int) -> None:
    steps = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
    cur = app._app_state.review_image.zoom
    idx = min(range(len(steps)), key=lambda i: abs(steps[i] - cur))
    idx = max(0, min(len(steps) - 1, idx + direction))
    app._app_state.review_image.zoom = steps[idx]
    app._render_review_image_display()

