"""Montage interaction helpers for WellViewerApp."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QToolTip
from functools import partial
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget
import numpy as _np
import logging
from ui.theme import CLR_SLATE_BG, CLR_SLATE_TEXT, CLR_SUCCESS_BG_DARK, CLR_SUCCESS_TEXT_SOFT
from well_viewer import debug_flags as _debug_flags
from well_viewer.image_discovery import open_imgref_as_array
from well_viewer.ui_helpers import (
    clear_layout as _clear_layout,
    make_fluor_thumb,
    make_overlay_thumb,
)


def montage_tophat_done(app, filtered_arrays: list, partial: bool = False) -> None:
    app._montage_fluor_display_arrays = filtered_arrays
    if not partial:
        valid = [a for a in filtered_arrays if a is not None]
        if valid and app._NP_AVAILABLE:
            lo = min(float(app._np.asarray(a).min()) for a in valid)
            hi = max(float(app._np.asarray(a).max()) for a in valid)
            if hi <= lo:
                hi = lo + 1.0
            app._mon_lmin_edit.setText(f"{lo:.0f}")
            app._mon_lmax_edit.setText(f"{hi:.0f}")

    fov = app._preview_fov_cb.currentText()
    tp_list = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
    if tp_list:
        draw_montage_thumbs(app, tp_list)

    if not partial:
        app._set_status(
            f"Top-hat filter applied  ·  {len(filtered_arrays)} frame(s)  ·  "
            f"radius={app._mon_tophat_radius_edit.text()} px"
        )


def montage_auto_lut(app, redraw: bool = True, force: bool = True) -> None:
    """Recompute the fluor + overlay LUT line edits from loaded arrays.

    When ``force`` is True (the default — used by the "Auto LUT" button),
    each line edit is overwritten with the pooled min/max across the active
    image source. When False (used by _refresh_preview_montage on well/FOV
    change), the existing user-set values are preserved if they parse as
    floats so the LUT survives navigation; only fields the user hasn't
    populated get auto-filled.
    """
    tophat_on = getattr(app, "_mon_tophat_cb", None) is not None and app._mon_tophat_cb.isChecked()
    display_arrays_exist = (
        tophat_on
        and hasattr(app, "_montage_fluor_display_arrays")
        and len(app._montage_fluor_display_arrays) == len(app._montage_fluor_arrays)
        and any(a is not None for a in app._montage_fluor_display_arrays)
    )
    source = [a for a in (app._montage_fluor_display_arrays if display_arrays_exist else app._montage_fluor_arrays) if a is not None]
    if not source:
        return

    def _is_float_text(edit) -> bool:
        try:
            float(edit.text())
            return True
        except (ValueError, AttributeError):
            return False

    fluor_set = (
        not force
        and _is_float_text(app._mon_lmin_edit)
        and _is_float_text(app._mon_lmax_edit)
    )
    if not fluor_set:
        lo = min(float(app._np.asarray(a).min()) for a in source)
        hi = max(float(app._np.asarray(a).max()) for a in source)
        if hi <= lo:
            hi = lo + 1.0
        app._mon_lmin_edit.setText(f"{lo:.0f}")
        app._mon_lmax_edit.setText(f"{hi:.0f}")

    # Overlay LUT: pool per-image min/max so the window is consistent across
    # timepoints, matching the fluor channel behaviour above.
    ov_lmin_edit = getattr(app, "_mon_ov_lmin_edit", None)
    ov_lmax_edit = getattr(app, "_mon_ov_lmax_edit", None)
    if ov_lmin_edit is not None and ov_lmax_edit is not None:
        ov_source = [a for a in getattr(app, "_montage_overlay_arrays", []) if a is not None]
        if ov_source:
            overlay_set = (
                not force
                and _is_float_text(ov_lmin_edit)
                and _is_float_text(ov_lmax_edit)
            )
            if not overlay_set:
                ov_lo = min(float(app._np.asarray(a).min()) for a in ov_source)
                ov_hi = max(float(app._np.asarray(a).max()) for a in ov_source)
                if ov_hi <= ov_lo:
                    ov_hi = ov_lo + 1.0
                ov_lmin_edit.setText(f"{ov_lo:.0f}")
                ov_lmax_edit.setText(f"{ov_hi:.0f}")

    if redraw:
        fov = app._preview_fov_cb.currentText()
        tp_list = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
        if tp_list:
            draw_montage_thumbs(app, tp_list)


def on_montage_canvas_resize(app, _e=None) -> None:
    timer = getattr(app, "_montage_resize_timer", None)
    if timer is not None and timer.isActive():
        timer.stop()
    if app._montage_fluor_arrays:
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(partial(montage_resize_deferred, app))
        t.start(150)
        app._montage_resize_timer = t


def montage_resize_deferred(app) -> None:
    app._montage_resize_timer = None
    fov = app._preview_fov_cb.currentText()
    tp_list = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
    if tp_list:
        draw_montage_thumbs(app, tp_list)


def on_montage_fluor_motion(app, event) -> None:
    _show_image_pixel_tooltip(
        app,
        event=event,
        channel_label=f"{app._app_state.channel.active_image_channel}".upper() or "IMAGE",
        label=getattr(app, "_montage_fluor_lbl", None),
    )


def _show_image_pixel_tooltip(app, event, channel_label: str, label) -> None:
    """Show x/y/value tooltip for a QLabel carrying `_raw_arr`, `_sz_w`, `_sz_h`."""
    lbl = label
    arr = getattr(lbl, "_raw_arr", None)
    if arr is None or not app._NP_AVAILABLE:
        QToolTip.hideText()
        return
    sz_w = getattr(lbl, "_sz_w", lbl.width() if lbl else 1)
    sz_h = getattr(lbl, "_sz_h", lbl.height() if lbl else 1)
    arr = app._np.asarray(arr, dtype=app._np.float32)
    full_h, full_w = arr.shape[:2]
    crop = getattr(lbl, "_crop", None)
    if crop is not None:
        # Pixmap displays only the cropped region; scale and offset must be
        # computed against the cropped dimensions, then offset back into
        # full-image coords for the reported (x, y).
        cy0, cx0, cy1, cx1 = crop
        view_w = max(1, int(cx1) - int(cx0))
        view_h = max(1, int(cy1) - int(cy0))
        offset_y, offset_x = int(cy0), int(cx0)
    else:
        view_w, view_h = full_w, full_h
        offset_y = offset_x = 0
    scale = min(sz_w / max(view_w, 1), sz_h / max(view_h, 1))
    nw, nh = max(1, int(view_w * scale)), max(1, int(view_h * scale))
    lw = lbl.width() if lbl else sz_w
    lh = lbl.height() if lbl else sz_h
    ex = int(event.position().x())
    ey = int(event.position().y())
    rel_x = (ex - (lw - nw) // 2) / max(scale, 1e-9)
    rel_y = (ey - (lh - nh) // 2) / max(scale, 1e-9)
    if not (0 <= rel_x < view_w and 0 <= rel_y < view_h):
        QToolTip.hideText()
        return
    img_x = offset_x + rel_x
    img_y = offset_y + rel_y
    val = float(arr[int(img_y), int(img_x)])
    extra = ""
    mask_arr = getattr(lbl, "_mask_arr", None)
    if mask_arr is not None:
        try:
            nid = int(app._np.asarray(mask_arr)[int(img_y), int(img_x)])
            if nid > 0:
                extra = f"  cell:{nid}"
        except Exception:
            pass
    global_pos = lbl.mapToGlobal(QPoint(ex, ey))
    QToolTip.showText(global_pos, f"x={int(img_x)}  y={int(img_y)}  {channel_label}:{val:.1f}{extra}")


def _wheel_steps(event) -> int:
    """Normalize Qt wheel event to signed integer step count."""
    try:
        delta = event.angleDelta().y()
    except AttributeError:
        return 0
    if delta == 0:
        return 0
    steps = max(1, abs(delta) // 120)
    return steps if delta > 0 else -steps


def montage_zoom_step(app, direction: int) -> None:
    cur = float(getattr(app, "_montage_zoom", 1.0) or 1.0)
    factor = 1.15
    if direction > 0:
        app._montage_zoom = min(16.0, cur * factor)
    elif direction < 0:
        app._montage_zoom = max(0.05, cur / factor)
    montage_redraw_at_zoom(app)


def montage_zoom_steps(app, steps: int) -> None:
    if steps == 0:
        return
    cur = float(getattr(app, "_montage_zoom", 1.0) or 1.0)
    app._montage_zoom = min(16.0, max(0.05, cur * (1.15 ** steps)))
    montage_redraw_at_zoom(app)


def montage_zoom_fit(app) -> None:
    app._montage_zoom = 1.0
    montage_redraw_at_zoom(app)


def on_montage_wheel(app, event) -> None:
    montage_zoom_steps(app, _wheel_steps(event))


def on_montage_shift_wheel(app, event) -> None:
    steps = _wheel_steps(event)
    if steps and hasattr(app, "_montage_scroll_area"):
        sb = app._montage_scroll_area.horizontalScrollBar()
        sb.setValue(sb.value() - steps * 60)


def montage_redraw_at_zoom(app) -> None:
    fov = app._preview_fov_cb.currentText()
    if fov == "—" or not app._montage_fluor_arrays:
        return
    tp_list = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
    if tp_list:
        draw_montage_thumbs(app, tp_list)


_logger = logging.getLogger(__name__)


def refresh_preview_montage(app) -> None:
    """
    Load images for the selected well + FOV and render the inline montage:
    two rows per timepoint column — fluorescence (top) and overlay (bottom).
    """
    if not hasattr(app, "_montage_inner"):
        return

    # Reset zoom to fit on each new well load
    app._montage_zoom = 1.0
    if hasattr(app, "_montage_zoom_lbl"):
        app._montage_zoom_lbl.setText("100%")

    # Preserve _montage_crop and the user-set LUT across well/FOV changes
    # so a region of interest stays selected as the user sweeps the data.
    # The crop is in pixel coords; make_fluor_thumb already clamps it
    # against each image's bounds, so a slightly off-image crop simply
    # shrinks instead of erroring on differently-sized FOVs. Refresh the
    # indicator so the status label still reflects the current crop.
    if hasattr(app, "_refresh_montage_crop_indicator"):
        app._refresh_montage_crop_indicator()

    # Clear previous content
    _clear_layout(app._montage_inner.layout())
    app._app_state.preview.montage_photos.clear()
    app._montage_fluor_arrays         = []
    app._montage_overlay_arrays     = []
    app._montage_fluor_display_arrays = []   # cleared on new load
    app._montage_th_status          = []
    app._montage_th_overlay_lbls    = []
    app._montage_th_cancel          = True   # no thread running initially

    well = app._app_state.preview.selected_well
    if well is None:
        app._montage_status.setText("Select a well in the left panel.")
        return

    fov = app._preview_fov_var.currentText()
    if fov == "—":
        app._montage_status.setText("No images found for this well.")
        return

    montage_load_debug = (
        _debug_flags.movie_montage_debug_enabled()
        or _debug_flags.movie_montage_load_debug_enabled()
    )

    # Filter refs to this FOV. Prefer pre-generated tophat frames from the
    # out directory for each timepoint, with raw fluor as fallback.
    raw_by_tp = {
        tp: ref for (f, tp), ref in sorted(app._app_state.preview.fluor.items())
        if f == fov
    }
    tophat_refs = getattr(app, "_preview_tophat_fluor", {})
    tophat_by_tp = {
        tp: ref for (f, tp), ref in sorted(tophat_refs.items())
        if f == fov
    }
    ordered_tps = list(raw_by_tp.keys())
    for tp in tophat_by_tp.keys():
        if tp not in ordered_tps:
            ordered_tps.append(tp)
    fluor_refs = [(tp, tophat_by_tp.get(tp) or raw_by_tp.get(tp)) for tp in ordered_tps]
    fluor_refs = [(tp, ref) for tp, ref in fluor_refs if ref is not None]
    _used_tophat_as_primary = bool(fluor_refs) and all(tp in tophat_by_tp for tp, _ in fluor_refs)

    overlay_refs = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.overlay.items())
                    if f == fov]
    # Align by timepoint (use all TPs from GFP; overlay may be subset)
    ov_map = dict(overlay_refs)
    n = len(fluor_refs)

    if n == 0:
        app._montage_status.setText("No images for this FOV.")
        return

    if montage_load_debug:
        _debug_flags.debug_with_source(
            _logger,
            "Movie Montage candidate refs for well=%s fov=%s :: fluor=%d tophat=%d overlay=%d",
            well,
            fov,
            len(raw_by_tp),
            len(tophat_by_tp),
            len(ov_map),
        )
        for tp, ref in fluor_refs:
            _debug_flags.debug_with_source(
                _logger,
                "Movie Montage load attempt fluor tp=%s path=%s",
                tp,
                getattr(ref, "full_path_str", str(ref)),
            )
        for tp, ref in overlay_refs:
            _debug_flags.debug_with_source(
                _logger,
                "Movie Montage load attempt overlay tp=%s path=%s",
                tp,
                getattr(ref, "full_path_str", str(ref)),
            )

    app._montage_status.setText(f"Loading {n} timepoint(s)…")
    QApplication.processEvents()

    app._montage_fluor_refs     = [ref for _, ref in fluor_refs]
    app._montage_overlay_refs = [ov_map.get(tp) for tp, _ in fluor_refs]

    # Load arrays
    app._montage_fluor_arrays = [
        open_imgref_as_array(ref, greyscale=True) for ref in app._montage_fluor_refs
    ]
    app._montage_overlay_arrays = [
        (open_imgref_as_array(ref) if ref else None)
        for ref in app._montage_overlay_refs
    ]
    if montage_load_debug:
        for ref in app._montage_overlay_refs:
            if ref is None:
                continue
            _debug_flags.debug_with_source(
                _logger,
                "Movie Montage loaded overlay path=%s",
                getattr(ref, "full_path_str", str(ref)),
            )

    # Loaded fluorescence refs already apply tophat-first selection.
    app._montage_fluor_display_arrays = list(app._montage_fluor_arrays)
    app._montage_tophat_preloaded = _used_tophat_as_primary or any(
        tp in tophat_by_tp for tp, _ in fluor_refs
    )

    app._montage_status.setText("")
    # Preserve user-set LUT values across well/FOV changes; only auto-fill
    # fluor/overlay LUTs when the line edits don't already hold a number.
    app._montage_auto_lut(redraw=False, force=False)
    app._update_tophat_controls()        # sync UI to actual preload result
    draw_montage_thumbs(app, [(tp, _) for tp, _ in fluor_refs])


def draw_montage_thumbs(app, tp_list: list) -> None:
    """Render fluorescence + overlay thumbnail pairs, one column per timepoint."""
    _clear_layout(app._montage_inner.layout())
    app._app_state.preview.montage_photos.clear()
    # Overlay label refs must be rebuilt each time since all widgets are destroyed
    app._montage_th_overlay_lbls = []

    try:
        lo = float(app._mon_lmin_entry.text())
    except ValueError:
        lo = None
    try:
        hi = float(app._mon_lmax_entry.text())
    except ValueError:
        hi = None

    ov_lmin_edit = getattr(app, "_mon_ov_lmin_entry", None)
    ov_lmax_edit = getattr(app, "_mon_ov_lmax_entry", None)
    try:
        ov_lo = float(ov_lmin_edit.text()) if ov_lmin_edit is not None else None
    except (ValueError, AttributeError):
        ov_lo = None
    try:
        ov_hi = float(ov_lmax_edit.text()) if ov_lmax_edit is not None else None
    except (ValueError, AttributeError):
        ov_hi = None

    # Use pre-filtered display arrays: either loaded from disk (preloaded)
    # or computed on-the-fly by the tophat thread.
    preloaded = getattr(app, "_montage_tophat_preloaded", False)
    use_display = (
        preloaded
        or (
            getattr(app, "_mon_tophat_cb", None) is not None
            and app._mon_tophat_cb.isChecked()
            and hasattr(app, "_montage_fluor_display_arrays")
            and len(app._montage_fluor_display_arrays) == len(app._montage_fluor_arrays)
        )
    )
    display_source = (app._montage_fluor_display_arrays
                      if use_display else app._montage_fluor_arrays)

    # Compute thumb size: base size × zoom factor.
    # Base size is the width that fits all timepoints in the canvas at 1×.
    cw = app._montage_canvas.viewport().width() or 400
    n  = len(tp_list)
    GAP = 6
    fit_sz = max(60, (cw - GAP) // max(n, 1) - GAP)
    app._montage_base_sz = fit_sz
    zoom   = getattr(app, "_montage_zoom", 1.0)
    sz_w   = max(40, int(fit_sz * zoom))
    sz_h   = max(35, int(sz_w * 0.8))
    if hasattr(app, "_montage_zoom_lbl"):
        app._montage_zoom_lbl.setText(f"{int(zoom * 100)}%")

    grid = app._montage_inner.layout()
    if grid is not None and not isinstance(grid, QGridLayout):
        # Re-parent the stale (e.g. QHBoxLayout left over from a tophat
        # refresh) to a throwaway so we can install a fresh QGridLayout.
        _tmp = QWidget()
        _tmp.setLayout(grid)
        _tmp.deleteLater()
        grid = None
    if grid is None:
        grid = QGridLayout(app._montage_inner)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
    # Keep the labelled rows/columns at their natural content size. Without
    # these the scroll area's widgetResizable=True grows the grid to fill
    # the viewport, spreads extra space evenly across every row/column, and
    # leaves the "GFP"/"overlay" labels floating in whitespace instead of
    # sitting immediately left of the thumbnails.
    grid.setRowStretch(3, 1)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(len(tp_list) + 1, 1)

    channel_row_lbl = QLabel(app._app_state.channel.active_image_channel.upper())
    channel_row_lbl.setObjectName("Muted")
    channel_row_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    grid.addWidget(channel_row_lbl, 1, 0)
    overlay_row_lbl = QLabel("overlay")
    overlay_row_lbl.setObjectName("Muted")
    overlay_row_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    grid.addWidget(overlay_row_lbl, 2, 0)

    def _install_montage_events(w, is_fluor):
        def _wheel(ev):
            if ev.modifiers() & Qt.ShiftModifier:
                on_montage_shift_wheel(app, ev)
            else:
                on_montage_wheel(app, ev)
        w.wheelEvent = _wheel
        if is_fluor:
            def _move(ev):
                # When mid-drag in crop mode, pump the rubber band update
                # instead of the pixel-tooltip helper so the box tracks
                # the cursor without flickering tooltips on top of it.
                if (
                    getattr(app, "_montage_crop_mode", False)
                    and app._montage_crop_drag is not None
                ):
                    app._on_montage_crop_drag(ev)
                    return
                on_montage_fluor_motion(app, ev)
            def _press(ev):
                if getattr(app, "_montage_crop_mode", False):
                    app._on_montage_crop_press(w, ev)
                    return
            def _release(ev):
                if (
                    getattr(app, "_montage_crop_mode", False)
                    and app._montage_crop_drag is not None
                ):
                    app._on_montage_crop_release(ev)
                    return
            def _leave(ev):
                try:
                    app._montage_tooltip.hide()
                except Exception:
                    pass
            w.setMouseTracking(True)
            w.mouseMoveEvent = _move
            w.mousePressEvent = _press
            w.mouseReleaseEvent = _release
            w.leaveEvent = _leave

    for col_idx, ((tp, _), fluor_arr, ov_arr) in enumerate(
            zip(tp_list, display_source, app._montage_overlay_arrays)):

        tp_lbl = QLabel(tp)
        tp_lbl.setObjectName("Muted")
        tp_lbl.setAlignment(Qt.AlignCenter)
        grid.addWidget(tp_lbl, 0, col_idx + 1)

        # fluorescence thumbnail
        fluor_cell = QFrame()
        fluor_cell.setFrameShape(QFrame.Box)
        fluor_cell_layout = QVBoxLayout(fluor_cell)
        fluor_cell_layout.setContentsMargins(1, 1, 1, 1)
        grid.addWidget(fluor_cell, 1, col_idx + 1)
        display_arr = fluor_arr
        crop = getattr(app, "_montage_crop", None)
        crop_mode = bool(getattr(app, "_montage_crop_mode", False))
        # In crop-selection mode, render the FULL FOV with a box overlay
        # showing where the active crop sits, so the user can always see
        # where the selection will appear and adjust it. Outside crop
        # mode, the thumbnail itself is the cropped/zoomed view.
        apply_crop = crop if not crop_mode else None
        pix_fluor = make_fluor_thumb(display_arr, sz_w, sz_h, lo, hi, crop=apply_crop)
        if pix_fluor and crop_mode and crop is not None and display_arr is not None:
            # Draw the active crop box on top of the full-FOV pixmap.
            pm = QPixmap(pix_fluor)
            painter = QPainter(pm)
            pen = QPen(QColor(255, 80, 80))
            pen.setWidth(2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            ih, iw = _np.asarray(display_arr).shape[:2]
            pw, ph = pm.width(), pm.height()
            scale_x = pw / max(iw, 1)
            scale_y = ph / max(ih, 1)
            y0, x0, y1, x1 = crop
            rx = int(round(x0 * scale_x))
            ry = int(round(y0 * scale_y))
            rw = max(1, int(round((x1 - x0) * scale_x)))
            rh = max(1, int(round((y1 - y0) * scale_y)))
            painter.drawRect(rx, ry, rw, rh)
            painter.end()
            pix_fluor = pm
        if pix_fluor:
            app._app_state.preview.montage_photos.append(pix_fluor)
            lbl_fluor = QLabel()
            lbl_fluor.setPixmap(pix_fluor)
            lbl_fluor.setCursor(Qt.CrossCursor)
            lbl_fluor._raw_arr = display_arr  # type: ignore[attr-defined]
            lbl_fluor._sz_w    = sz_w        # type: ignore[attr-defined]
            lbl_fluor._sz_h    = sz_h        # type: ignore[attr-defined]
            lbl_fluor._lo      = lo          # type: ignore[attr-defined]
            lbl_fluor._hi      = hi          # type: ignore[attr-defined]
            # When painting the full FOV with an overlay, leave _crop
            # unset so the pixel-tooltip / rubber-band coord conversions
            # operate against the full image.
            lbl_fluor._crop    = apply_crop  # type: ignore[attr-defined]
            fluor_cell_layout.addWidget(lbl_fluor)
            _install_montage_events(lbl_fluor, is_fluor=True)
        else:
            miss = QLabel(f"{app._app_state.channel.active_image_channel.upper()}\nunavail")
            miss.setObjectName("Muted")
            miss.setAlignment(Qt.AlignCenter)
            fluor_cell_layout.addWidget(miss)

        th_on = (getattr(app, "_mon_tophat_cb", None) is not None
                 and app._mon_tophat_cb.isChecked())
        th_state = (app._montage_th_status[col_idx]
                    if col_idx < len(app._montage_th_status) else "")
        if th_on and th_state == "pending":
            overlay_txt, overlay_bg, overlay_fg = "\u23f3 filtering\u2026", CLR_SLATE_BG, CLR_SLATE_TEXT
        elif th_on and th_state == "done":
            overlay_txt, overlay_bg, overlay_fg = "\u2713 filtered", CLR_SUCCESS_BG_DARK, CLR_SUCCESS_TEXT_SOFT
        else:
            overlay_txt = ""
        if overlay_txt:
            th_lbl = QLabel(overlay_txt, fluor_cell)
            th_lbl.setStyleSheet(f"background: {overlay_bg}; color: {overlay_fg}; padding: 1px 4px;")
            th_lbl.setAlignment(Qt.AlignCenter)
            th_lbl.show()
            app._montage_th_overlay_lbls.append(th_lbl)
        else:
            app._montage_th_overlay_lbls.append(None)

        ov_cell = QFrame()
        ov_cell.setFrameShape(QFrame.Box)
        ov_cell_layout = QVBoxLayout(ov_cell)
        ov_cell_layout.setContentsMargins(1, 1, 1, 1)
        grid.addWidget(ov_cell, 2, col_idx + 1)
        pix_ov = make_overlay_thumb(ov_arr, sz_w, sz_h, ov_lo, ov_hi, crop=apply_crop)
        if pix_ov and crop_mode and crop is not None and ov_arr is not None:
            # Mirror the box overlay on the overlay row so the user can
            # see the same selection on both fluor and segmentation.
            pm = QPixmap(pix_ov)
            painter = QPainter(pm)
            pen = QPen(QColor(255, 80, 80))
            pen.setWidth(2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            _ov_arr_np = _np.asarray(ov_arr)
            ih, iw = _ov_arr_np.shape[:2]
            pw, ph = pm.width(), pm.height()
            scale_x = pw / max(iw, 1)
            scale_y = ph / max(ih, 1)
            y0, x0, y1, x1 = crop
            rx = int(round(x0 * scale_x))
            ry = int(round(y0 * scale_y))
            rw = max(1, int(round((x1 - x0) * scale_x)))
            rh = max(1, int(round((y1 - y0) * scale_y)))
            painter.drawRect(rx, ry, rw, rh)
            painter.end()
            pix_ov = pm
        if pix_ov:
            app._app_state.preview.montage_photos.append(pix_ov)
            lbl_ov = QLabel()
            lbl_ov.setPixmap(pix_ov)
            ov_cell_layout.addWidget(lbl_ov)
            _install_montage_events(lbl_ov, is_fluor=False)
        else:
            miss = QLabel("overlay\nunavail")
            miss.setObjectName("Muted")
            miss.setAlignment(Qt.AlignCenter)
            ov_cell_layout.addWidget(miss)
    # Send surplus horizontal space to a virtual empty column at the far
    # right so col 0 (labels) stays pinned against the first image column
    # instead of drifting when the grid is narrower than the viewport.
    for ci in range(grid.columnCount()):
        grid.setColumnStretch(ci, 0)
    grid.setColumnStretch(n + 1, 1)
    n_ov = sum(1 for a in app._montage_overlay_arrays if a is not None)
    app._montage_status.setText(
        f"{n} timepoint(s)  \u00b7  {n_ov} overlay(s)")



def update_tophat_controls(app, preloaded: Optional[bool] = None) -> None:
    """Sync the top-hat row UI to the actual preload state.

    *preloaded* = True  → tophat images were loaded from disk for this FOV;
                          lock controls, show badge, display filtered images.
    *preloaded* = False → no preloaded images; enable manual filtering.
    *preloaded* = None  → read from app._montage_tophat_preloaded (default).

    This is the single source of truth for the tophat control state.
    Called from _refresh_preview_montage (after FOV-level coverage is known)
    and from _update_preview (to reset before a new well loads).
    """
    if not hasattr(app, "_th_checkbox"):
        return
    if preloaded is None:
        preloaded = getattr(app, "_montage_tophat_preloaded", False)

    # Block toggled() so the programmatic state sync doesn't re-kick off
    # the tophat filter thread via _montage_tophat_toggled.
    _prev = app._th_checkbox.blockSignals(True)
    try:
        if preloaded:
            app._th_checkbox.setChecked(True)
            app._th_checkbox.setEnabled(False)
            app._th_checkbox.setText("Top-hat background subtraction")
            app._th_radius_entry.setEnabled(False)
            app._th_preload_badge.setText("\u25cf from output zip")
        else:
            app._th_checkbox.setChecked(False)
            app._th_checkbox.setEnabled(True)
            app._th_checkbox.setText("Top-hat background subtraction")
            app._th_radius_entry.setEnabled(True)
            app._th_preload_badge.setText("")
    finally:
        app._th_checkbox.blockSignals(_prev)

