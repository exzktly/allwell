"""Playground tab — controller (logic, event handlers, no Qt-app import).

Drives the single-frame correction testbed: populate the well/channel/timepoint/
fov selectors (reusing the Image Table option helpers), load the chosen RAW frame
and its precomputed segmentation mask (reusing the Image Table loaders), apply the
chosen correction via the shared ``process_microscopy.correct_single_frame``
(identical code path to the batch pipeline), and render through the Playground
renderer. Reference-well methods (empty_well / basic) build their per-channel
field on a worker thread.

Widget reads are all guarded (``getattr``) so the controller can be driven purely
through ``app._app_state.playground`` in headless tests.
"""

from __future__ import annotations

import numpy as np

import process_microscopy as pm
from well_viewer.features.image_browser.image_table.controller import (
    _channel_options, _fov_options, _load_array, _load_mask_array,
    _timepoint_options, _well_options,
)
from well_viewer.features.image_browser.playground.renderer import (
    build_playground_rgb, render_playground_display,
)
from well_viewer.features.image_browser.playground.worker import request_reference_fields


def _st(app):
    return app._app_state.playground


def _is_real_channel(label: str) -> bool:
    """Exclude the virtual NUC+SEG overlay and ratio channels — the Playground
    corrects real single-channel frames only."""
    return bool(label) and label != "NUC+SEG" and "/" not in label


# ── dropdown population ──────────────────────────────────────────────────────

def _set_combo(app, attr: str, options, current):
    cb = getattr(app, attr, None)
    if cb is None:
        return
    from well_viewer.core.ui_helpers import set_combo_values
    set_combo_values(cb, options or ["—"])
    if current and cb.findText(str(current)) >= 0:
        blocked = cb.blockSignals(True)
        try:
            cb.setCurrentText(str(current))
        finally:
            cb.blockSignals(blocked)


def playground_repopulate_dropdowns(app) -> None:
    """Refresh the frame selectors from the loaded dataset (idempotent)."""
    st = _st(app)
    wells = _well_options(app)
    channels = [c for c in _channel_options(app) if _is_real_channel(c)]
    tps = _timepoint_options(app)
    fovs = _fov_options(app)
    st.well = st.well or (wells[0] if wells else None)
    st.channel = st.channel or (channels[0] if channels else None)
    st.timepoint = st.timepoint or (tps[0] if tps else None)
    st.fov = st.fov or (fovs[0] if fovs else None)
    _set_combo(app, "_playground_well_cb", wells, st.well)
    _set_combo(app, "_playground_chan_cb", channels, st.channel)
    _set_combo(app, "_playground_tp_cb", tps, st.timepoint)
    _set_combo(app, "_playground_fov_cb", fovs, st.fov)


# ── widget → state ───────────────────────────────────────────────────────────

def _read_combo(app, attr: str):
    cb = getattr(app, attr, None)
    if cb is None:
        return None
    txt = str(cb.currentText() or "").strip()
    return txt if txt and txt != "—" else None


def _read_float(app, attr: str, default: float) -> float:
    from well_viewer.core.ui_helpers import parse_float_edit
    return parse_float_edit(getattr(app, attr, None), default)


def _read_widgets_into_state(app) -> None:
    """Pull current widget values into state. No-op for widgets not built yet
    (so headless tests can set ``app._app_state.playground`` fields directly)."""
    st = _st(app)
    for attr, field in (("_playground_well_cb", "well"),
                        ("_playground_chan_cb", "channel"),
                        ("_playground_tp_cb", "timepoint"),
                        ("_playground_fov_cb", "fov")):
        v = _read_combo(app, attr)
        if v is not None:
            setattr(st, field, v)
    bg_cb = getattr(app, "_playground_bg_cb", None)
    if bg_cb is not None:
        st.background_method = str(bg_cb.currentText() or "none")
    ff_cb = getattr(app, "_playground_ff_cb", None)
    if ff_cb is not None:
        st.flatfield_mode = str(ff_cb.currentText() or "none")
    bw = getattr(app, "_playground_bgwell_edit", None)
    if bw is not None:
        st.background_well = str(bw.text() or "").strip() or None
    st.radius = int(_read_float(app, "_playground_radius_edit", st.radius))
    st.fft_small = _read_float(app, "_playground_fft_small_edit", st.fft_small)
    st.mesh_ratio = _read_float(app, "_playground_mesh_ratio_edit", st.mesh_ratio)
    st.mesh_sigma = _read_float(app, "_playground_mesh_sigma_edit", st.mesh_sigma)
    st.qc_min_area = _read_float(app, "_playground_qc_min_area_edit", st.qc_min_area)
    st.qc_max_area = _read_float(app, "_playground_qc_max_area_edit", st.qc_max_area)
    lut = getattr(app, "_playground_cmap_cb", None)
    if lut is not None and hasattr(lut, "lut"):
        name = lut.lut()
        st.cmap_name = (name + "_r") if (hasattr(lut, "isReversed") and lut.isReversed()) else name


# ── LUT ──────────────────────────────────────────────────────────────────────

def _resolve_lut(app, arr):
    """Parse the playground LUT min/max edits ('auto'/blank/number); fall back to
    the array's min/max. Mirrors review_image_resolve_lut."""
    def _num(attr, fallback):
        e = getattr(app, attr, None)
        if e is None:
            return fallback
        txt = str(e.text() or "").strip().lower()
        if not txt or txt == "auto":
            return fallback
        try:
            return float(txt)
        except ValueError:
            return fallback
    a = np.asarray(arr, dtype=np.float32)
    lo = _num("_playground_lmin_entry", float(a.min()) if a.size else 0.0)
    hi = _num("_playground_lmax_entry", float(a.max()) if a.size else 1.0)
    if hi <= lo:
        hi = lo + 1.0
    # NB: do NOT write the computed range back over an "auto" entry. Keeping
    # "auto" live means every correction-parameter change re-derives the window
    # from the current frame, so subtle changes (e.g. robust_mesh tile/sigma)
    # are reflected on screen instead of being masked by a stale, locked LUT.
    return lo, hi


def _set_status(app, text: str) -> None:
    from well_viewer.core.ui_helpers import set_widget_text
    set_widget_text(app, "_playground_status", text)


# ── main render ──────────────────────────────────────────────────────────────

def playground_on_change(app) -> None:
    """Reload + recorrect + rerender on any selector / param / toggle change."""
    st = _st(app)
    _read_widgets_into_state(app)
    if not (st.well and st.channel and st.timepoint and st.fov):
        _set_status(app, "Select a well, channel, timepoint, and FOV.")
        return
    if app._app_state.dataset.data_dir is None:
        _set_status(app, "Load a dataset first.")
        return

    cache = st.image_cache
    raw = _load_array(app, cache, st.well, st.channel, st.timepoint, st.fov,
                      use_tophat=False)
    if raw is None:
        st.raw_arr = None
        _set_status(app, "No raw image for the selected frame.")
        return
    st.raw_arr = np.asarray(raw, dtype=np.float32)
    st.mask_arr = _load_mask_array(app, cache, st.well, st.timepoint, st.fov)
    frame_lbl = f"{st.well} {st.channel} t{st.timepoint} fov{st.fov}"

    # The binary threshold is per-frame: when the frame identity changes, drop
    # any local override so the control falls back to the channel's app-wide
    # ThreshFracOn default, and refresh the displayed value.
    frame_key = (st.well, st.channel, st.timepoint, st.fov)
    if getattr(app, "_playground_frame_key", None) != frame_key:
        app._playground_frame_key = frame_key
        st.threshold = None
        _refresh_threshold_display(app)

    # Raw — no correction.
    if st.source == "raw":
        _render(app, st.raw_arr)
        _set_status(app, f"Raw — {frame_lbl}")
        return

    chan_lower = st.channel.lower()
    if _source_needs_reference(st):
        if not st.background_well:
            _set_status(app, "Pick a background well for empty_well / basic.")
            _render(app, st.raw_arr)
            return
        if st.model_well != st.background_well:
            _build_reference_then_render(app, chan_lower)
            return
        ff = st.ff_field_by_chan.get(chan_lower)
        bg = st.bg_field_by_chan.get(chan_lower)
    else:
        ff = bg = None

    # Flat-field source applies only the flat-field (background = none).
    bg_method = st.background_method if st.source == "corrected" else "none"
    corrected = pm.correct_single_frame(
        st.raw_arr, flatfield_mode=st.flatfield_mode,
        background_method=bg_method, radius=st.radius,
        fft_small=st.fft_small, mesh_ratio=st.mesh_ratio, mesh_sigma=st.mesh_sigma,
        ff_field=ff, bg_field=bg)
    _render(app, corrected)
    label = ("Flat-field" if st.source == "flatfield"
             else f"Corrected ({st.flatfield_mode}+{st.background_method})")
    _set_status(app, f"{label} — {frame_lbl}")


def _source_needs_reference(st) -> bool:
    """A reference-well field is needed for empty_well/basic flat-field (any
    non-raw source) and for empty_well background (corrected source only)."""
    if st.source == "raw":
        return False
    if st.flatfield_mode in ("empty_well", "basic"):
        return True
    return st.source == "corrected" and st.background_method == "empty_well"


def _effective_threshold(app) -> float:
    """Frame-local binary threshold: the user's override if set, else the
    channel's app-wide ThreshFracOn as a read-only default (never written back)."""
    st = _st(app)
    if st.threshold is not None:
        return float(st.threshold)
    try:
        from well_viewer.core.channel_state_controller import get_thresh_frac_on
        return float(get_thresh_frac_on(app, (st.channel or "").lower()))
    except Exception:
        return 0.0


def _qc_size_filtered(mask, min_area: float, max_area: float):
    """Return *mask* with labels whose pixel area falls outside [min_area,
    max_area] zeroed out (0 = no bound). Mirrors the pipeline's size-based
    debris gate so the Playground can preview it. Returns the mask unchanged
    when no bound is set."""
    if mask is None or (min_area <= 0 and max_area <= 0):
        return mask
    center = np.asarray(mask, dtype=np.int32)
    counts = np.bincount(center.ravel())
    keep = np.ones(counts.shape[0], dtype=bool)
    if min_area > 0:
        keep &= counts >= float(min_area)
    if max_area > 0:
        keep &= counts <= float(max_area)
    keep[0] = True                         # background label stays 0 either way
    drop = np.where(~keep)[0]
    if drop.size == 0:
        return center
    out = center.copy()
    out[np.isin(center, drop)] = 0
    return out


def _cells_above(arr, mask, threshold: float):
    """Label ids whose mean intensity in *arr* exceeds *threshold* (per-cell
    gate over the precomputed mask). None when the mask doesn't match the frame."""
    center = np.asarray(mask, dtype=np.int32)
    if center.shape[:2] != np.asarray(arr).shape[:2]:
        return None
    ids = np.unique(center)
    ids = ids[ids > 0]
    if ids.size == 0:
        return set()
    from scipy import ndimage as ndi
    means = np.atleast_1d(ndi.mean(np.asarray(arr, dtype=np.float32),
                                   labels=center, index=ids))
    return {int(i) for i, m in zip(ids, means) if float(m) > threshold}


def _render(app, arr) -> None:
    st = _st(app)
    st.display_arr = np.asarray(arr, dtype=np.float32)
    lo, hi = _resolve_lut(app, arr)
    # Apply the QC size-based cleanup to the mask before display, so the overlay
    # and binary reflect the debris gate.
    qc_mask = _qc_size_filtered(st.mask_arr, st.qc_min_area, st.qc_max_area)
    mask = qc_mask if (st.show_overlay or st.binary_mask) else None
    above_ids = None
    thr = None
    if st.binary_mask:
        thr = _effective_threshold(app)
        if qc_mask is not None:
            above_ids = _cells_above(arr, qc_mask, thr)
    st.base_rgb = build_playground_rgb(
        arr, mask, lo=lo, hi=hi, cmap_name=st.cmap_name, binary=st.binary_mask,
        show_boundary=st.show_overlay, tint=tuple(st.tint_color),
        above_ids=above_ids, threshold=thr)
    render_playground_display(app)


def _build_reference_then_render(app, chan_lower: str) -> None:
    """Kick off the reference-field worker; re-render when it lands."""
    st = _st(app)
    _set_status(app, f"Building {st.flatfield_mode}/{st.background_method} model "
                     f"from well {st.background_well}…")
    try:
        from well_viewer.core.status_signal import warn_push
        warn_push()
        app._playground_warn_pushed = True
    except Exception:
        app._playground_warn_pushed = False

    def _on_done(well, chan, ff_field, bg_field):
        if ff_field is not None:
            st.ff_field_by_chan[chan] = ff_field
        if bg_field is not None:
            st.bg_field_by_chan[chan] = bg_field
        st.model_well = st.background_well
        if getattr(app, "_playground_warn_pushed", False):
            try:
                from well_viewer.core.status_signal import warn_pop
                warn_pop()
            finally:
                app._playground_warn_pushed = False
        playground_on_change(app)

    def _on_failed(msg):
        if getattr(app, "_playground_warn_pushed", False):
            try:
                from well_viewer.core.status_signal import warn_pop
                warn_pop()
            finally:
                app._playground_warn_pushed = False
        _set_status(app, f"Model build failed: {msg}")

    app._playground_ref_bridge = request_reference_fields(
        app, st.background_well, chan_lower,
        flatfield_mode=st.flatfield_mode, background_method=st.background_method,
        on_done=_on_done, on_failed=_on_failed)


def _refresh_threshold_display(app) -> None:
    """Sync the Threshold stepper to the effective threshold (signals blocked so
    the display sync doesn't trigger a re-render)."""
    stp = getattr(app, "_playground_threshold_stepper", None)
    if stp is None:
        return
    blocked = stp.blockSignals(True)
    try:
        stp.setValue(_effective_threshold(app))
    finally:
        stp.blockSignals(blocked)


def _event_xy(event):
    try:
        pos = event.position()          # Qt6 QMouseEvent
        return float(pos.x()), float(pos.y())
    except AttributeError:
        return float(event.x()), float(event.y())


def playground_pan_press(app, event) -> None:
    """Record the drag origin so the next moves pan relative to it."""
    st = _st(app)
    mx, my = _event_xy(event)
    app._playground_pan_drag = (mx, my, float(st.pan_x), float(st.pan_y))


def playground_pan_move(app, event) -> None:
    """Pan the image by the drag delta (view is kept in state, so it persists
    across source/parameter changes)."""
    drag = getattr(app, "_playground_pan_drag", None)
    if drag is None:
        return
    st = _st(app)
    mx, my = _event_xy(event)
    sx, sy, ox, oy = drag
    st.pan_x = ox + (mx - sx)
    st.pan_y = oy + (my - sy)
    render_playground_display(app)


def playground_hover(app, event) -> None:
    """Show a tooltip with the hovered pixel's intensity in the displayed frame."""
    st = _st(app)
    arr = st.display_arr
    disp = getattr(app, "_playground_disp", None)
    label = getattr(app, "_playground_image_label", None)
    if arr is None or disp is None or label is None:
        return
    a = np.asarray(arr)
    nw, nh, iw, ih = disp["nw"], disp["nh"], disp["iw"], disp["ih"]
    mx, my = _event_xy(event)
    # The pixmap is centred in the (possibly larger) label.
    off_x = max(0, (label.width() - nw) // 2)
    off_y = max(0, (label.height() - nh) // 2)
    px = int((mx - off_x) * iw / max(nw, 1))
    py = int((my - off_y) * ih / max(nh, 1))
    if not (0 <= px < a.shape[1] and 0 <= py < a.shape[0]):
        return
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QToolTip
    QToolTip.showText(QCursor.pos(), f"({px}, {py})  =  {float(a[py, px]):.1f}")
