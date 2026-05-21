"""Preview/montage callback handlers (Qt port)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QToolTip, QVBoxLayout, QWidget

_logger = logging.getLogger("well_viewer")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def _make_fluor_pixmap(arr, sz_w: int, sz_h: int, lo, hi):
    """Convert a float32 greyscale array → QPixmap with LUT mapping."""
    try:
        import numpy as _np
        from PIL import Image as _Img
    except ImportError:
        return None
    if arr is None:
        return None
    arr = _np.asarray(arr, dtype=_np.float32)
    if lo is None:
        lo = float(arr.min())
    if hi is None:
        hi = float(arr.max())
    if hi <= lo:
        hi = lo + 1.0
    arr_n = _np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(_np.uint8)
    pil = _Img.fromarray(arr_n, mode="L").resize((sz_w, sz_h), _Img.BILINEAR)
    rgb = _np.stack([_np.array(pil)] * 3, axis=-1).copy()
    img = QImage(rgb.data, sz_w, sz_h, sz_w * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(img)


def _make_overlay_pixmap(arr, sz_w: int, sz_h: int):
    """Convert an RGB/RGBA/grey array → QPixmap."""
    try:
        import numpy as _np
        from PIL import Image as _Img
    except ImportError:
        return None
    if arr is None:
        return None
    arr = _np.asarray(arr)
    if arr.dtype != _np.uint8:
        arr = _np.clip(arr, 0, 255).astype(_np.uint8)
    if arr.ndim == 2:
        arr = _np.stack([arr] * 3, axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    pil = _Img.fromarray(arr, mode="RGB").resize((sz_w, sz_h), _Img.BILINEAR)
    rgb = _np.array(pil).copy()
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(img)


# ---------------------------------------------------------------------------
# Montage thumbnail QLabel that handles wheel / motion events
# ---------------------------------------------------------------------------
class _MontageThumbLabel(QLabel):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        # Delegate to montage controller's tooltip helper
        if hasattr(self._app, "_on_montage_fluor_motion"):
            self._app._on_montage_fluor_motion(event)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        mods = event.modifiers()
        if mods & Qt.ShiftModifier:
            if hasattr(self._app, "_on_montage_shift_wheel"):
                self._app._on_montage_shift_wheel(event)
        else:
            if hasattr(self._app, "_on_montage_wheel"):
                self._app._on_montage_wheel(event)
        event.accept()


# ---------------------------------------------------------------------------
# Layout clear helper
# ---------------------------------------------------------------------------
def _clear_widget(widget: QWidget) -> None:
    layout = widget.layout()
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


# ---------------------------------------------------------------------------
# refresh_preview_montage
# ---------------------------------------------------------------------------
def refresh_preview_montage(app) -> None:
    from well_viewer import debug_flags as _debug_flags

    if not hasattr(app, "_montage_inner"):
        return
    montage_debug = _debug_flags.movie_montage_debug_enabled()
    app._montage_zoom = 1.0
    if hasattr(app, "_montage_zoom_lbl"):
        app._montage_zoom_lbl.setText("100%")
    _clear_widget(app._montage_inner)
    app._montage_fluor_arrays = []
    app._montage_overlay_arrays = []
    app._montage_fluor_display_arrays = []
    app._montage_th_status = []
    app._montage_th_overlay_lbls = []
    app._montage_th_cancel = True

    well = app._app_state.preview.selected_well
    if well is None:
        app._montage_status.setText("Select a well in the left panel.")
        return
    fov = app._preview_fov_cb.currentText()
    if montage_debug:
        _logger.debug("refresh_preview_montage selected_fov=%r", fov)
    if fov == "—":
        app._montage_status.setText("No images found for this well.")
        return

    fluor_refs = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
    tophat_refs = getattr(app, "_preview_tophat_fluor", {})
    used_tophat_as_primary = False
    if not fluor_refs:
        fluor_refs = [(tp, ref) for (f, tp), ref in sorted(tophat_refs.items()) if f == fov]
        used_tophat_as_primary = bool(fluor_refs)

    overlay_refs = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.overlay.items()) if f == fov]
    ov_map = dict(overlay_refs)
    n = len(fluor_refs)
    if n == 0:
        app._montage_status.setText("No images for this FOV.")
        return
    app._montage_status.setText(f"Loading {n} timepoint(s)…")
    QApplication.processEvents()

    app._montage_fluor_refs = [ref for _, ref in fluor_refs]
    app._montage_overlay_refs = [ov_map.get(tp) for tp, _ in fluor_refs]
    app._montage_fluor_arrays = [app._open_imgref_as_array(ref, greyscale=True) for ref in app._montage_fluor_refs]
    app._montage_overlay_arrays = [(app._open_imgref_as_array(ref) if ref else None) for ref in app._montage_overlay_refs]

    if used_tophat_as_primary:
        app._montage_fluor_display_arrays = list(app._montage_fluor_arrays)
        app._montage_tophat_preloaded = True
    else:
        raw_by_tp = {tp: arr for (tp, _), arr in zip(fluor_refs, app._montage_fluor_arrays)}
        app._montage_fluor_display_arrays = []
        used_any_tophat = False
        for tp, _ in fluor_refs:
            th_ref = tophat_refs.get((fov, tp))
            if th_ref is not None:
                app._montage_fluor_display_arrays.append(app._open_imgref_as_array(th_ref, greyscale=True))
                used_any_tophat = True
            else:
                app._montage_fluor_display_arrays.append(raw_by_tp[tp])
        app._montage_tophat_preloaded = used_any_tophat

    app._montage_status.setText("")
    app._montage_auto_lut(redraw=False)
    app._update_tophat_controls()
    draw_montage_thumbs(app, [(tp, _) for tp, _ in fluor_refs])


# ---------------------------------------------------------------------------
# draw_montage_thumbs
# ---------------------------------------------------------------------------
def draw_montage_thumbs(app, tp_list: list) -> None:
    _clear_widget(app._montage_inner)
    app._montage_th_overlay_lbls = []

    try:
        lo = float(app._mon_lmin_edit.text())
    except (ValueError, AttributeError):
        lo = None
    try:
        hi = float(app._mon_lmax_edit.text())
    except (ValueError, AttributeError):
        hi = None

    preloaded = getattr(app, "_montage_tophat_preloaded", False)
    use_display = preloaded or (
        hasattr(app, "_mon_tophat_cb") and app._mon_tophat_cb.isChecked()
        and hasattr(app, "_montage_fluor_display_arrays")
        and len(app._montage_fluor_display_arrays) == len(app._montage_fluor_arrays)
    )
    display_source = app._montage_fluor_display_arrays if use_display else app._montage_fluor_arrays

    scroll_area = getattr(app, "_montage_scroll_area", None)
    cw = scroll_area.viewport().width() if scroll_area else 400
    n = len(tp_list)
    gap = 6
    fit_sz = max(60, (cw - gap) // max(n, 1) - gap)
    app._montage_base_sz = fit_sz
    zoom = getattr(app, "_montage_zoom", 1.0)
    sz_w = max(40, int(fit_sz * zoom))
    sz_h = max(35, int(sz_w * 0.8))
    if hasattr(app, "_montage_zoom_lbl"):
        app._montage_zoom_lbl.setText(f"{int(zoom * 100)}%")

    outer_layout = app._montage_inner.layout()
    if outer_layout is None:
        from PySide6.QtWidgets import QHBoxLayout
        outer_layout = QHBoxLayout(app._montage_inner)
        outer_layout.setContentsMargins(4, 4, 4, 4)
        outer_layout.setSpacing(gap)

    for col_idx, ((tp, _), fluor_arr, ov_arr) in enumerate(zip(tp_list, display_source, app._montage_overlay_arrays)):
        col_widget = QWidget(app._montage_inner)
        cl = QVBoxLayout(col_widget)
        cl.setContentsMargins(2, 2, 2, 2)
        cl.setSpacing(2)

        tp_lbl = QLabel(str(tp), col_widget)
        tp_lbl.setObjectName("Muted")
        tp_lbl.setAlignment(Qt.AlignCenter)
        cl.addWidget(tp_lbl)

        # Fluor thumbnail
        pm_fluor = _make_fluor_pixmap(fluor_arr, sz_w, sz_h, lo, hi)
        if pm_fluor is not None:
            lbl_fluor = _MontageThumbLabel(app, col_widget)
            lbl_fluor.setPixmap(pm_fluor)
            lbl_fluor._raw_arr = fluor_arr
            lbl_fluor._sz_w = sz_w
            lbl_fluor._sz_h = sz_h
            cl.addWidget(lbl_fluor)
        else:
            na = QLabel(f"{app._app_state.channel.active_image_channel.upper()}\nunavail", col_widget)
            na.setObjectName("Muted")
            na.setAlignment(Qt.AlignCenter)
            na.setFixedSize(sz_w, sz_h)
            cl.addWidget(na)

        # Top-hat status label
        th_on = hasattr(app, "_mon_tophat_cb") and app._mon_tophat_cb.isChecked()
        th_state = app._montage_th_status[col_idx] if col_idx < len(app._montage_th_status) else ""
        if th_on and th_state in ("pending", "done"):
            overlay_txt = "⏳ filtering…" if th_state == "pending" else "✓ filtered"
            th_lbl = QLabel(overlay_txt, col_widget)
            th_lbl.setObjectName("Muted")
            th_lbl.setAlignment(Qt.AlignCenter)
            cl.addWidget(th_lbl)
            app._montage_th_overlay_lbls.append(th_lbl)
        else:
            app._montage_th_overlay_lbls.append(None)

        # Overlay thumbnail
        pm_ov = _make_overlay_pixmap(ov_arr, sz_w, sz_h)
        if pm_ov is not None:
            lbl_ov = _MontageThumbLabel(app, col_widget)
            lbl_ov.setPixmap(pm_ov)
            cl.addWidget(lbl_ov)
        else:
            na2 = QLabel("overlay\nunavail", col_widget)
            na2.setObjectName("Muted")
            na2.setAlignment(Qt.AlignCenter)
            na2.setFixedSize(sz_w, sz_h)
            cl.addWidget(na2)

        cl.addStretch(1)
        outer_layout.addWidget(col_widget)

    outer_layout.addStretch(1)
    n_ov = sum(1 for a in app._montage_overlay_arrays if a is not None)
    app._montage_status.setText(f"{n} timepoint(s)  ·  {n_ov} overlay(s)")


# ---------------------------------------------------------------------------
# montage_tophat_toggled
# ---------------------------------------------------------------------------
class _TophatBridge(QObject):
    frame_done = Signal(int, int)   # step, idx
    visible_done = Signal()
    all_done = Signal()


def montage_tophat_toggled(app) -> None:
    import threading as _threading

    if not app._montage_fluor_arrays:
        return
    app._montage_th_cancel = True

    if not (hasattr(app, "_mon_tophat_cb") and app._mon_tophat_cb.isChecked()):
        app._montage_th_cancel = True
        app._montage_fluor_display_arrays = []
        app._montage_th_status = []
        app._montage_th_overlay_lbls = []
        app._montage_auto_lut(redraw=True)
        app._set_status("Top-hat filter removed.")
        return

    try:
        radius = max(1, int(app._mon_tophat_radius_edit.text()))
    except (ValueError, AttributeError):
        radius = 50
    try:
        from skimage.morphology import white_tophat as _wth, disk as _sk_disk
        _selem = _sk_disk(radius)
    except ImportError:
        _logger.warning("Top-hat requested but scikit-image not installed. pip install scikit-image")
        app._mon_tophat_cb.setChecked(False)
        return

    try:
        import numpy as _np
    except ImportError:
        return

    raw_arrays = list(app._montage_fluor_arrays)
    n_total = len(raw_arrays)
    display = list(raw_arrays)
    app._montage_th_status = ["pending"] * n_total
    app._montage_th_cancel = False

    fov = app._preview_fov_cb.currentText()
    tp_list = [(tp, ref) for (f, tp), ref in sorted(app._app_state.preview.fluor.items()) if f == fov]
    if tp_list:
        app._draw_montage_thumbs(tp_list)

    bridge = _TophatBridge()

    def _update_th_lbl(idx: int, state: str) -> None:
        if idx >= len(app._montage_th_overlay_lbls):
            return
        lbl = app._montage_th_overlay_lbls[idx]
        if lbl is None:
            return
        if state == "done":
            lbl.setText("✓ filtered")
        elif state == "error":
            lbl.setText("✗ error")
        else:
            lbl.setText("⏳ filtering…")

    def _on_frame_done(step: int, idx: int) -> None:
        if getattr(app, "_montage_th_cancel", True):
            return
        st = app._montage_th_status[idx] if idx < len(app._montage_th_status) else ""
        _update_th_lbl(idx, st)
        app._set_status(f"Top-hat filter: {step}/{n_total} frame(s)…")

    def _on_visible_done() -> None:
        if not getattr(app, "_montage_th_cancel", True):
            app._montage_tophat_done(list(display), partial=True)

    def _on_all_done() -> None:
        if not getattr(app, "_montage_th_cancel", True):
            app._montage_tophat_done(display, partial=False)

    bridge.frame_done.connect(_on_frame_done)
    bridge.visible_done.connect(_on_visible_done)
    bridge.all_done.connect(_on_all_done)

    # Determine visible range for priority processing
    scroll_area = getattr(app, "_montage_scroll_area", None)
    if scroll_area:
        sb = scroll_area.horizontalScrollBar()
        mv = sb.maximum()
        x0 = sb.value() / mv if mv else 0.0
        x1 = (sb.value() + sb.pageStep()) / mv if mv else 1.0
    else:
        x0, x1 = 0.0, 1.0
    visible = [i for i in range(n_total) if x0 <= (i + 0.5) / max(n_total, 1) <= x1] or list(range(n_total))
    others = [i for i in range(n_total) if i not in set(visible)]
    ordered = visible + others

    def _worker() -> None:
        for step, i in enumerate(ordered, 1):
            if getattr(app, "_montage_th_cancel", True):
                return
            if raw_arrays[i] is not None:
                try:
                    display[i] = _wth(_np.asarray(raw_arrays[i], dtype=_np.float32), _selem)
                    if i < len(app._montage_th_status):
                        app._montage_th_status[i] = "done"
                except Exception as exc:
                    _logger.error("Top-hat frame %d failed: %s", i, exc)
                    if i < len(app._montage_th_status):
                        app._montage_th_status[i] = "error"
            bridge.frame_done.emit(step, i)
            if step == len(visible) and others:
                bridge.visible_done.emit()
        bridge.all_done.emit()

    app._montage_status.setText("")
    _threading.Thread(target=_worker, daemon=True).start()
