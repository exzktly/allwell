"""Qt event handlers for the Segmentation (review-image) canvas."""

from __future__ import annotations

from typing import List, Tuple

import logging

from well_viewer.features.image_browser.review_image.renderer import refresh_review_image_ctx
from well_viewer.features.image_browser.review_image.renderer import render_review_image_display
from well_viewer.features.image_browser.review_image.renderer import (
    review_image_get_color, review_image_set_color,
)
from well_viewer.features.image_browser.review_image.box_select import (
    box_select_begin, box_select_finish, box_select_update,
)
from well_viewer.core.channel_state_controller import set_active_image_channel
import numpy as _np

from well_viewer.core.safe import swallowed

# Pure data/query helpers live in review_image/collect.py (T4). The Qt event
# handlers below + external callers (load_controller, the tab views) import
# these from here, so re-export them.
from well_viewer.features.image_browser.review_image.collect import (  # noqa: E402,F401
    _binary_gate_columns,
    _flatfield_qc,
    _load_arrays_from_refs,
    _split_background_wells,
    apply_review_overrides_to_cache,
    flatfield_field_for,
    review_build_include_map,
    review_build_threshold_map,
    review_collect_timepoints,
    review_effective_included,
    review_load_rows,
    review_norm_fov,
    review_resolve_image_refs,
    review_row_keys,
    review_row_keys_series,
)

_logger = logging.getLogger(__name__)


def _guard_event(fn):
    """Wrap a Qt mouse-event handler so an exception can't escape into the C++
    event dispatch — under PySide6 that aborts the whole app (the "clicking a
    cell in the Segmentation tab crashes" report). Log the traceback instead so
    the click is a no-op and the cause is captured."""
    import functools

    @functools.wraps(fn)
    def _wrapped(app, event, *args, **kwargs):
        try:
            return fn(app, event, *args, **kwargs)
        except Exception:  # noqa: BLE001 — never let a click kill the app
            _logger.exception("review-image %s failed", getattr(fn, "__name__", fn))
            return None

    return _wrapped



@_guard_event
def on_review_image_click(ctx, event, logger) -> None:
    label = ctx.runtime.review_image.label
    mask_arr = getattr(label, "_mask_arr", None)
    if mask_arr is None:
        return
    scale = float(ctx.runtime.review_image.scale or 1.0)
    off_x, off_y = 0, 0
    pm = label.pixmap()
    if pm is not None and not pm.isNull():
        off_x = max(0, (label.width() - pm.width()) // 2)
        off_y = max(0, (label.height() - pm.height()) // 2)
    x = int((event.position().x() - off_x) / scale)
    y = int((event.position().y() - off_y) / scale)
    if y < 0 or x < 0 or y >= mask_arr.shape[0] or x >= mask_arr.shape[1]:
        return
    nid = int(mask_arr[y, x])
    if nid <= 0:
        return
    fov_menu = ctx.runtime.review_image.fov_menu or ctx.widgets.get_or_none("_preview_fov_cb")
    fov = fov_menu.currentText().strip() if fov_menu is not None else ""
    tp_cb = ctx.runtime.review_image.tp_cb
    tp = tp_cb.currentText().strip() if tp_cb is not None else ""
    if ctx.state.review_image.include_edit_mode:
        set_review_cell_included(ctx, fov, tp, str(nid), "0")
        ctx.set_status(f"Set Included=0 for nucleus {nid} at FOV {fov}, TP {tp}.")
        return
    ctx.state.review_image.selected_nucleus = nid
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)
    ctx.set_status(f"Selected nucleus {nid} at FOV {fov}, TP {tp}.")


@_guard_event
def on_review_image_press(ctx, event) -> None:
    # In include-edit (delete) or crop-edit mode, LMB drag draws a rubber-band
    # rectangle instead of panning. The anchor is recorded in label-local
    # pixels (invariant to window position / scrollbar state) by the shared
    # box-select helper; only the release action differs between the modes.
    ri = ctx.state.review_image
    if ri.include_edit_mode or ri.crop_edit_mode:
        box_select_begin(ctx, event)
        return
    ri.dragging = True
    ri.drag_moved = False
    gp = event.globalPosition().toPoint()
    ri.drag_last_xy = (int(gp.x()), int(gp.y()))


def on_review_image_drag(ctx, event) -> None:
    # Rubber-band update path: stay anchored in label-local pixels.
    ri = ctx.state.review_image
    if ri.box_active:
        box_select_update(ctx, event)
        return
    if not ri.dragging:
        return
    gp = event.globalPosition().toPoint()
    gx, gy = int(gp.x()), int(gp.y())
    lx, ly = ri.drag_last_xy
    dx = gx - lx
    dy = gy - ly
    if dx or dy:
        ri.drag_moved = True
    ri.pan_x = float(ri.pan_x + dx)
    ri.pan_y = float(ri.pan_y + dy)
    ri.drag_last_xy = (gx, gy)
    # Pan reuses the existing pixmap; only the scrollbars need to move.
    render_review_image_display(ctx, pan_only=True)


@_guard_event
def on_review_image_release(ctx, event) -> None:
    # Rubber-band finalize path, shared by delete and crop modes. A drag too
    # short to be a box returns None: in delete mode that quick tap toggles one
    # cell (the existing click handler); in crop mode it is a no-op.
    ri = ctx.state.review_image
    if ri.box_active:
        crop_mode = ri.crop_edit_mode
        rect = box_select_finish(ctx, event)
        if rect is None:
            if not crop_mode:
                on_review_image_click(ctx, event, _logger)
            return
        mx0, my0, mx1, my1 = rect
        if crop_mode:
            # Crop stores a row/col region; release gives x=col, y=row.
            # Sanctioned deferred import: crop.controller imports back into
            # review_image, so keeping this function-local breaks the genuine
            # crop↔review_image cycle (counted by the deferred-import ratchet).
            from well_viewer.features.crop.controller import (
                set_crop_edit_mode, set_crop_region,
            )
            set_crop_region(ctx, (my0, mx0, my1, mx1))
            # The crop is a one-shot draw — leave crop mode (and un-check the
            # Crop Region button) once the region is committed.
            set_crop_edit_mode(ctx, False)
        else:
            apply_box_delete(ctx, mx0, my0, mx1, my1)
        return
    was_dragging = ri.dragging
    moved = ri.drag_moved
    ri.dragging = False
    ri.drag_moved = False
    if was_dragging and not moved:
        on_review_image_click(ctx, event, _logger)


@_guard_event
def on_review_image_move(ctx, event) -> None:
    """Unified mouseMove handler: drag when LMB held, otherwise hover."""
    from PySide6.QtCore import Qt
    if event.buttons() & Qt.LeftButton:
        on_review_image_drag(ctx, event)
    else:
        on_review_image_hover(ctx, event)


def _show_image_pixel_tooltip(ctx, event, channel_label: str, label) -> None:
    """Show an x/y/value tooltip for a QLabel carrying ``_raw_arr`` / ``_sz_w`` /
    ``_sz_h`` (and optionally ``_crop`` / ``_mask_arr``).

    Previously lived in the now-removed Movie Montage module; it is kept here as
    the review-image hover is its only live caller.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QToolTip

    lbl = label
    # Prefer the array stashed on the label; fall back to the last-drawn array
    # on state so a hover still works if the label handle was reset between
    # renders. Both are the *displayed* array, so the readout matches the
    # active view mode (raw / flat-field / corrected).
    arr = getattr(lbl, "_raw_arr", None)
    if arr is None:
        arr = getattr(ctx.state.review_image, "last_fluor_arr", None)
    if arr is None or _np is None:
        QToolTip.hideText()
        return
    sz_w = getattr(lbl, "_sz_w", lbl.width() if lbl else 1)
    sz_h = getattr(lbl, "_sz_h", lbl.height() if lbl else 1)
    arr = _np.asarray(arr, dtype=_np.float32)
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
            nid = int(_np.asarray(mask_arr)[int(img_y), int(img_x)])
            if nid > 0:
                extra = f"  cell:{nid}"
        except Exception:
            swallowed()  # was: pass (silent except)
    global_pos = lbl.mapToGlobal(QPoint(ex, ey))
    QToolTip.showText(global_pos, f"x={int(img_x)}  y={int(img_y)}  {channel_label}:{val:.1f}{extra}")


def on_review_image_hover(ctx, event) -> None:
    label = ctx.runtime.review_image.label
    if label is None:
        return
    pm = label.pixmap()
    if pm is not None and not pm.isNull():
        label._sz_w = int(pm.width())
        label._sz_h = int(pm.height())
    else:
        label._sz_w = int(label.width())
        label._sz_h = int(label.height())
    _show_image_pixel_tooltip(
        ctx,
        event,
        str(ctx.state.channel.active_image_channel or "").upper(),
        label=label,
    )


def on_review_image_channel_selected(ctx, _e=None) -> None:
    """Channel-switch handler that preserves Review Image zoom/pan view."""
    import logging
    from well_viewer.core import debug_flags as _debug_flags
    logger = logging.getLogger(__name__)
    if _debug_flags.review_image_channel_switch_debug_enabled():
        logger.debug("[RI-CHSW step 1] Review Image channel ComboboxSelected event received")
    selected_ui_value = ""
    chan_cb = ctx.runtime.review_image.chan_cb
    if chan_cb is not None:
        selected_ui_value = str(chan_cb.currentText() or "").strip()
    chan_var = ctx.runtime.review_image.chan_var
    if not selected_ui_value and chan_var is not None:
        selected_ui_value = chan_var.currentText()
    if _debug_flags.review_image_channel_switch_debug_enabled():
        logger.debug(
            "[RI-CHSW step 2] review_image_channel_selected ui_value=%r active_before=%r",
            selected_ui_value,
            ctx.state.channel.active_image_channel,
        )
    set_active_image_channel(ctx, selected_ui_value.lower(), preserve_review_view=True)


# ── Flat-field validation view (recompute the selected flat-field) ────────────


def pick_review_image_color(ctx, which: str) -> None:
    """Open a v2 ColorSwatchRow popover anchored at the swatch button."""
    from PySide6.QtGui import QColor

    from widgets.color_swatch_row import ColorSwatchRow
    from widgets.popover import Popover

    anchor = (ctx.runtime.review_image.color_swatches or {}).get(which)
    if anchor is None:
        return

    r, g, b = review_image_get_color(ctx, which)
    recents = list(ctx.runtime.review_image.color_recents or [])

    pop = Popover(anchor)
    row = ColorSwatchRow(allow_custom=True, recents=recents)
    row.setCurrentColor(QColor(int(r), int(g), int(b)))

    def _apply(c: QColor, _which=which, _pop=pop, _row=row) -> None:
        if not c.isValid():
            return
        review_image_set_color(ctx, _which, (c.red(), c.green(), c.blue()))
        rec = [c] + [QColor(x) for x in (ctx.runtime.review_image.color_recents or [])
                     if QColor(x).rgb() != c.rgb()]
        ctx.runtime.review_image.color_recents = rec[:8]

    row.colorPicked.connect(_apply)
    pop.setContentWidget(row)
    pop.popup(anchor, side="bottom", align="start")


def set_review_cell_included(ctx, fov: str, tp: str, nid: str, included: str) -> None:
    from well_viewer.core.selection_controller import invalidate_stats_cache
    if ctx.state.preview.selected_well is None:
        return
    fov_n, tp_n, nid_n = review_row_keys(ctx, {"fov": fov, "tp": tp, "nucleus_id": nid})
    if not (fov_n and tp_n and nid_n):
        return
    key = (ctx.state.preview.selected_well, fov_n, tp_n, nid_n)
    ctx.runtime.review.included_overrides[key] = str(included).strip() or "1"
    # Project the override onto the cached row immediately so other tabs
    # (Bar/Line/Distribution/Stats/Heatmap) honor it without waiting for
    # the next gating recompute, then invalidate the stats cache.
    apply_review_overrides_to_cache(ctx, ctx.state.preview.selected_well)
    try:
        invalidate_stats_cache(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    # Persist to disk (debounced) so curation survives between sessions.
    ctx.persistence.schedule_save("cell_overrides")
    # Bump the override version so the include-map cache is bypassed
    # on the next refresh; the frame cache (decoded image + boundary)
    # remains valid because the underlying mask label image is unchanged.
    ctx.state.review_image.override_version += 1
    prev_zoom = float(ctx.state.review_image.zoom)
    prev_pan_x = float(ctx.state.review_image.pan_x)
    prev_pan_y = float(ctx.state.review_image.pan_y)
    # Skip a full Review CSV table rebuild here — the user is on the
    # Review Image tab and rebuilding tens of thousands of QTableWidgetItems
    # per click was the dominant memory leak. The table reads the effective
    # Included via override on the next refresh (combobox change / Refresh
    # button / well-selection change), so the value is never lost.
    refresh_review_image_ctx(ctx)
    ctx.state.review_image.zoom = prev_zoom
    ctx.state.review_image.pan_x = prev_pan_x
    ctx.state.review_image.pan_y = prev_pan_y
    render_review_image_display(ctx)


def set_review_cells_included_batch(
    ctx, items: List[Tuple[str, str, str, str]]
) -> None:
    """Apply many (fov, tp, nid, included) overrides with a single redraw.

    Mirrors _set_review_cell_included but coalesces the per-call
    cache-projection, stats invalidation, save-schedule, and image
    refresh — otherwise a 100-cell box delete would redraw 100 times.
    """
    from well_viewer.core.selection_controller import invalidate_stats_cache
    if ctx.state.preview.selected_well is None or not items:
        return
    well = ctx.state.preview.selected_well
    any_changed = False
    for fov, tp, nid, included in items:
        fov_n, tp_n, nid_n = review_row_keys(
            ctx, {"fov": fov, "tp": tp, "nucleus_id": nid}
        )
        if not (fov_n and tp_n and nid_n):
            continue
        ctx.runtime.review.included_overrides[(well, fov_n, tp_n, nid_n)] = (
            str(included).strip() or "1"
        )
        any_changed = True
    if not any_changed:
        return
    apply_review_overrides_to_cache(ctx, well)
    try:
        invalidate_stats_cache(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)
    ctx.persistence.schedule_save("cell_overrides")
    ctx.state.review_image.override_version += 1
    prev_zoom = float(ctx.state.review_image.zoom)
    prev_pan_x = float(ctx.state.review_image.pan_x)
    prev_pan_y = float(ctx.state.review_image.pan_y)
    refresh_review_image_ctx(ctx)
    ctx.state.review_image.zoom = prev_zoom
    ctx.state.review_image.pan_x = prev_pan_x
    ctx.state.review_image.pan_y = prev_pan_y
    render_review_image_display(ctx)


def apply_box_delete(ctx, x0: int, y0: int, x1: int, y1: int) -> None:
    """Mark every cell with any pixel in mask[y0:y1, x0:x1] as Included=0.

    Coordinates are in mask-array pixel space (already converted from
    label-local pixels by _on_review_image_release using the current
    _review_image_scale, so window position, scroll, and zoom are
    already accounted for).
    """
    label = ctx.runtime.review_image.label
    mask = getattr(label, "_mask_arr", None) if label is not None else None
    if mask is None or ctx.state.preview.selected_well is None:
        return
    h, w = int(mask.shape[0]), int(mask.shape[1])
    xa, xb = sorted((max(0, min(w, int(x0))), max(0, min(w, int(x1)))))
    ya, yb = sorted((max(0, min(h, int(y0))), max(0, min(h, int(y1)))))
    if xb - xa < 1 or yb - ya < 1:
        return
    sub = mask[ya:yb, xa:xb]
    nids = [int(n) for n in _np.unique(sub) if int(n) > 0]
    if not nids:
        ctx.set_status("Box selection: no cells inside rectangle.")
        return
    fov_cb = ctx.runtime.review_image.fov_menu or ctx.widgets.get_or_none("_preview_fov_cb")
    if fov_cb is None:
        return
    fov = fov_cb.currentText().strip()
    tp_cb = ctx.runtime.review_image.tp_cb
    tp = tp_cb.currentText().strip() if tp_cb is not None else ""
    set_review_cells_included_batch(ctx,
        [(fov, tp, str(nid), "0") for nid in nids]
    )
    ctx.set_status(
        f"Box delete: marked {len(nids)} cell(s) Included=0."
    )


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


