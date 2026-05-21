"""Montage interaction helpers for WellViewerApp."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QToolTip


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
        app._draw_montage_thumbs(tp_list)

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
            app._draw_montage_thumbs(tp_list)


def on_montage_canvas_resize(app, _e=None) -> None:
    timer = getattr(app, "_montage_resize_timer", None)
    if timer is not None and timer.isActive():
        timer.stop()
    if app._montage_fluor_arrays:
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(app._montage_resize_deferred)
        t.start(150)
        app._montage_resize_timer = t


def montage_resize_deferred(app) -> None:
    app._montage_resize_timer = None
    fov = app._preview_fov_cb.currentText()
    tp_list = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
    if tp_list:
        app._draw_montage_thumbs(tp_list)


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
    app._montage_redraw_at_zoom()


def montage_zoom_steps(app, steps: int) -> None:
    if steps == 0:
        return
    cur = float(getattr(app, "_montage_zoom", 1.0) or 1.0)
    app._montage_zoom = min(16.0, max(0.05, cur * (1.15 ** steps)))
    app._montage_redraw_at_zoom()


def montage_zoom_fit(app) -> None:
    app._montage_zoom = 1.0
    app._montage_redraw_at_zoom()


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
        app._draw_montage_thumbs(tp_list)
