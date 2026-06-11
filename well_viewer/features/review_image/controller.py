"""Controller helpers for Review Image <-> Review CSV interactions."""

from __future__ import annotations

from typing import List, Optional

import logging

from PySide6.QtWidgets import QTableWidgetItem

from well_viewer.features.review_image.renderer import refresh_review_image
from well_viewer.features.review_image.renderer import update_preview
from well_viewer.features.review_image.box_select import (
    box_select_begin, box_select_finish, box_select_update,
)
from well_viewer.core.channel_state_controller import set_active_image_channel
from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values
from well_viewer.data.data_loading import parse_timepoint_hours
from well_viewer.data.image_resolver import resolve_ref_by_fov_tp as _resolve_ref_by_fov_tp
from well_viewer.core.ui_helpers import safe_int_or_none as _safe_int_or_none
import numpy as _np
import numpy as np
import pandas as pd

from well_viewer.data.data_loading import df_included_mask, resolve_value_series
from well_viewer.core.safe import swallowed

# Pure data/query helpers live in review_image/collect.py (T4). The Qt event
# handlers below + external callers (load_controller, the tab views) import
# these from here, so re-export them.
from well_viewer.features.review_image.collect import (  # noqa: E402,F401
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



def _select_tab_by_text(notebook, text: str) -> None:
    """Select a centre-stack page by its name (NamedPageStack v2 API)."""
    if notebook is None:
        return
    setter = getattr(notebook, "setCurrentByName", None)
    if setter is not None:
        setter(text)


def _table_column_names(table) -> list[str]:
    """Return the QTableWidget's header labels (index strings when unset)."""
    return [
        (table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else str(c))
        for c in range(table.columnCount())
    ]


def _table_row_dict(table, row_idx: int, col_names: list[str] | None = None) -> dict:
    """Extract a row from a QTableWidget as a {col_name: value} dict.

    Pass ``col_names`` (from :func:`_table_column_names`) when iterating many
    rows so the header labels aren't re-fetched for every row.
    """
    if col_names is None:
        col_names = _table_column_names(table)
    return {
        col_names[c]: (table.item(row_idx, c).text() if table.item(row_idx, c) else "")
        for c in range(min(len(col_names), table.columnCount()))
    }


@_guard_event
def on_review_image_click(app, event, logger) -> None:
    mask_arr = getattr(app._review_image_label, "_mask_arr", None)
    if mask_arr is None:
        return
    scale = float(getattr(app, "_review_image_scale", 1.0) or 1.0)
    label = app._review_image_label
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
    fov_menu = getattr(app, "_review_image_fov_menu", None) or getattr(app, "_preview_fov_cb", None)
    fov = fov_menu.currentText().strip() if fov_menu is not None else ""
    tp_cb = getattr(app, "_review_image_tp_cb", None)
    tp = tp_cb.currentText().strip() if tp_cb is not None else ""
    if app._app_state.review_image.include_edit_mode:
        set_review_cell_included(app,fov, tp, str(nid), "0")
        app._set_status(f"Set Included=0 for nucleus {nid} at FOV {fov}, TP {tp}.")
        return
    app._app_state.review_image.selected_nucleus = nid
    app._app_state.review_image.preserve_view_on_refresh = True
    refresh_review_image(app)
    app._select_review_csv_row_for_cell(fov, tp, str(nid))


def _resolve_table_column(col_names: list[str], *candidates: str) -> int:
    """Return the index of the first matching column (case-insensitive), or -1."""
    lowered = {name.lower(): i for i, name in enumerate(col_names)}
    for cand in candidates:
        if cand in col_names:
            return col_names.index(cand)
        i = lowered.get(cand.lower())
        if i is not None:
            return i
    return -1


def select_review_csv_row_for_cell(app, fov: str, tp: str, nucleus_id: str, logger) -> None:
    """Navigate the Review CSV tab to the row for the given cell.

    Wrapped end-to-end: any failure is logged + surfaced on the status line
    rather than escaping into the Qt event loop (which previously left the UI
    looking hung). The post-refresh row scan reads only the FOV / timepoint /
    nucleus columns instead of materialising a dict per row, so a fallback
    "show all rows" table doesn't make this O(rows × columns).
    """
    try:
        fov_n, tp_n, nucleus_n = app._review_row_keys({"fov": fov, "tp": tp, "nucleus_id": nucleus_id})
        app._review_csv_lookup_context = {
            "well": str(app._app_state.preview.selected_well or ""),
            "fov": str(fov_n),
            "tp": str(tp_n),
            "nucleus_id": str(nucleus_n),
        }
        app._set_status(
            "Review CSV lookup request: "
            f"well={app._review_csv_lookup_context['well']}  "
            f"fov={app._review_csv_lookup_context['fov']}  "
            f"tp={app._review_csv_lookup_context['tp']}  "
            f"nucleus_id={app._review_csv_lookup_context['nucleus_id']}"
        )
        logger.info(
            "Review-image click -> Review CSV lookup: well=%s fov=%s tp=%s nucleus_id=%s",
            app._app_state.preview.selected_well, fov, tp, nucleus_id,
        )
        # Ensure the previewed well is in the sidebar selection so the tab-switch
        # refresh loads its CSV rows — Review Image's preview well is independent
        # of _selected_wells, and _refresh_review_csv reads only _selected_wells.
        preview_well = app._app_state.preview.selected_well
        if (
            preview_well
            and preview_well in app._app_state.dataset.well_paths
            and preview_well not in app._app_state.selection.selected_wells
        ):
            # Go through the single mutation helper so _prev_sel is
            # snapshotted properly. commit=False because we're about to
            # switch tabs anyway — the tab-change handler will trigger
            # the redraw.
            if hasattr(app, "_set_selected_wells"):
                new_sel = set(app._app_state.selection.selected_wells) | {preview_well}
                app._set_selected_wells(new_sel, commit=False)
            else:
                app._app_state.selection.selected_wells.add(preview_well)
                if hasattr(app, "_refresh_sidebar_map"):
                    app._refresh_sidebar_map()
        # If the Review CSV tab is already built, pre-point its FOV/TP combos
        # at the target *before* switching tabs. The tab-switch fires
        # _refresh_review_csv, which would otherwise rebuild the table for
        # whatever FOV/TP happened to be selected (and, when none of those
        # rows survive the filter, fall back to inserting *every* loaded row —
        # tens of thousands of QTableWidgetItems — which is what made this
        # look hung).
        if hasattr(app, "_review_fov_cb") and hasattr(app, "_review_tp_cb"):
            for combo, value in ((app._review_fov_cb, fov_n), (app._review_tp_cb, tp_n)):
                combo.blockSignals(True)
                try:
                    combo.setCurrentText(value)
                finally:
                    combo.blockSignals(False)
        # Switch to the Review CSV tab — this lazily builds the tab (and its
        # combo boxes / table) if the user hasn't visited it yet.
        if hasattr(app, "_notebook"):
            _select_tab_by_text(app._notebook, "Review CSV")
        if not hasattr(app, "_review_csv_table") or not hasattr(app, "_review_fov_cb"):
            app._set_status("Review CSV tab is not available yet.")
            return
        # Block the per-combo currentIndexChanged signals so we rebuild the
        # table once here instead of once per setCurrentText.
        app._review_fov_cb.blockSignals(True)
        app._review_tp_cb.blockSignals(True)
        try:
            app._review_fov_cb.setCurrentText(fov_n)
            app._review_tp_cb.setCurrentText(tp_n)
        finally:
            app._review_fov_cb.blockSignals(False)
            app._review_tp_cb.blockSignals(False)
        app._refresh_review_csv_rows()

        table = app._review_csv_table
        col_names = _table_column_names(table)
        fov_ci = _resolve_table_column(col_names, "fov", "FOV")
        tp_ci = _resolve_table_column(col_names, "timepoint", "tp", "time", "time_h", "timepoint_hours")
        nid_ci = _resolve_table_column(col_names, "nucleus_id", "nucleus id", "nucleusId", "nucleusID")

        def _cell(row_idx: int, ci: int) -> str:
            if ci < 0:
                return ""
            item = table.item(row_idx, ci)
            return item.text() if item is not None else ""

        debug_candidates = []
        for row_idx in range(table.rowCount()):
            mini = {}
            if fov_ci >= 0:
                mini[col_names[fov_ci]] = _cell(row_idx, fov_ci)
            if tp_ci >= 0:
                mini[col_names[tp_ci]] = _cell(row_idx, tp_ci)
            if nid_ci >= 0:
                mini[col_names[nid_ci]] = _cell(row_idx, nid_ci)
            rf, rt, rn = app._review_row_keys(mini)
            if len(debug_candidates) < 10:
                debug_candidates.append((rf, rt, rn))
            if rf == fov_n and rt == tp_n and rn == nucleus_n:
                table.selectRow(row_idx)
                item = table.item(row_idx, 0)
                if item:
                    table.scrollToItem(item)
                app._set_status(f"Matched Review CSV row for nucleus {nucleus_n} at FOV {fov_n}, TP {tp_n}.")
                return
        logger.warning(
            "Review CSV exact row match not found. target=(%s,%s,%s) rows=%d sample=%s",
            fov_n, tp_n, nucleus_n, table.rowCount(), debug_candidates,
        )
        app._set_status(
            f"No exact Review CSV row match for nucleus {nucleus_n} at FOV {fov_n}, TP {tp_n}; showing fallback rows."
        )
    except Exception:  # noqa: BLE001
        logger.exception("Review-image -> Review CSV navigation failed")
        try:
            app._set_status("Could not navigate to the Review CSV row (see log for details).")
        except Exception:
            swallowed()  # was: pass (silent except)


def on_review_csv_row_double_click(app, item) -> None:
    """Called with a QTableWidgetItem from itemDoubleClicked signal.

    Wrapped end-to-end so a partially-built Segmentation tab (e.g. the
    user double-clicks before the preview panel has materialised) shows a
    status message instead of bubbling a NoneType AttributeError out of
    the Qt event loop.
    """
    try:
        _on_review_csv_row_double_click_impl(app, item)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("well_viewer").exception(
            "Review-CSV row double-click failed",
        )
        try:
            app._set_status(
                f"Could not navigate to Segmentation: {exc} (see log for details).",
            )
        except Exception:
            swallowed()  # was: pass (silent except)


def _on_review_csv_row_double_click_impl(app, item) -> None:
    if not hasattr(app, "_review_csv_table"):
        return
    table = app._review_csv_table
    row_idx = item.row()
    row = _table_row_dict(table, row_idx)
    fov, tp, nid = app._review_row_keys(row)
    well_tok = str(row.get("well", "")).strip()
    key = well_tok if well_tok in app._app_state.dataset.well_paths else None
    if key is None and app._app_state.selection.selected_wells:
        key = sorted(app._app_state.selection.selected_wells, key=app._parse_rc)[0]
    if key is None:
        return

    if hasattr(app, "_notebook"):
        # Segmentation is a plain top-level tab (the review image); jumps from
        # Review CSV land straight on it. (smFISH is its own rail entry now.)
        _select_tab_by_text(app._notebook, "Segmentation")

    # Set the target nucleus before any refresh so intermediate renders can
    # already draw the yellow highlight.
    if nid:
        try:
            app._app_state.review_image.selected_nucleus = int(float(nid))
        except Exception:
            app._app_state.review_image.selected_nucleus = None

    app._app_state.preview.selected_well = key
    update_preview(app, key)

    # _preview_fov_var drives _refresh_review_image; force the target FOV
    # before repopulating the TP menu. The combo only exists once the
    # Segmentation tab's preview panel has been built — guard for the
    # double-click-before-build case so we don't crash.
    if fov:
        fov_cb = getattr(app, "_preview_fov_cb", None)
        if fov_cb is not None:
            fov_cb.setCurrentText(fov)
    refresh_review_image(app)

    # TP menu now holds the target FOV's timepoints. Selecting the TP here
    # triggers refresh_review_image via currentIndexChanged, which renders
    # the correct frame with the highlight already in place.
    if tp:
        tp_cb = getattr(app, "_review_image_tp_cb", None)
        if tp_cb is not None:
            tp_cb.setCurrentText(tp)

    zoom_review_image_to_selected_nucleus(app)


def on_review_image_wheel(app, event) -> None:
    try:
        dy = int(event.angleDelta().y())
    except Exception:
        dy = 0
    direction = +1 if dy > 0 else -1 if dy < 0 else 0
    if direction:
        app._review_image_zoom_step(direction)


@_guard_event
def on_review_image_press(app, event) -> None:
    # In include-edit (delete) or crop-edit mode, LMB drag draws a rubber-band
    # rectangle instead of panning. The anchor is recorded in label-local
    # pixels (invariant to window position / scrollbar state) by the shared
    # box-select helper; only the release action differs between the modes.
    ri = app._app_state.review_image
    if ri.include_edit_mode or ri.crop_edit_mode:
        box_select_begin(app, event)
        return
    ri.dragging = True
    ri.drag_moved = False
    gp = event.globalPosition().toPoint()
    ri.drag_last_xy = (int(gp.x()), int(gp.y()))


def on_review_image_drag(app, event) -> None:
    # Rubber-band update path: stay anchored in label-local pixels.
    if app._app_state.review_image.box_active:
        box_select_update(app, event)
        return
    if not app._app_state.review_image.dragging:
        return
    gp = event.globalPosition().toPoint()
    gx, gy = int(gp.x()), int(gp.y())
    lx, ly = app._app_state.review_image.drag_last_xy
    dx = gx - lx
    dy = gy - ly
    if dx or dy:
        app._app_state.review_image.drag_moved = True
    app._app_state.review_image.pan_x = float(app._app_state.review_image.pan_x + dx)
    app._app_state.review_image.pan_y = float(app._app_state.review_image.pan_y + dy)
    app._app_state.review_image.drag_last_xy = (gx, gy)
    # Pan reuses the existing pixmap; only the scrollbars need to move.
    app._render_review_image_display(pan_only=True)


@_guard_event
def on_review_image_release(app, event) -> None:
    # Rubber-band finalize path, shared by delete and crop modes. A drag too
    # short to be a box returns None: in delete mode that quick tap toggles one
    # cell (the existing click handler); in crop mode it is a no-op.
    ri = app._app_state.review_image
    if ri.box_active:
        crop_mode = ri.crop_edit_mode
        rect = box_select_finish(app, event)
        if rect is None:
            if not crop_mode:
                app._on_review_image_click(event)
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
            set_crop_region(app, (my0, mx0, my1, mx1))
            # The crop is a one-shot draw — leave crop mode (and un-check the
            # Crop Region button) once the region is committed.
            set_crop_edit_mode(app, False)
        else:
            apply_box_delete(app, mx0, my0, mx1, my1)
        return
    was_dragging = app._app_state.review_image.dragging
    moved = app._app_state.review_image.drag_moved
    app._app_state.review_image.dragging = False
    app._app_state.review_image.drag_moved = False
    if was_dragging and not moved:
        app._on_review_image_click(event)


@_guard_event
def on_review_image_move(app, event) -> None:
    """Unified mouseMove handler: drag when LMB held, otherwise hover."""
    from PySide6.QtCore import Qt
    if event.buttons() & Qt.LeftButton:
        on_review_image_drag(app, event)
    else:
        on_review_image_hover(app, event)


def _show_image_pixel_tooltip(app, event, channel_label: str, label) -> None:
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
        arr = getattr(app._app_state.review_image, "last_fluor_arr", None)
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
            swallowed()  # was: pass (silent except)
    global_pos = lbl.mapToGlobal(QPoint(ex, ey))
    QToolTip.showText(global_pos, f"x={int(img_x)}  y={int(img_y)}  {channel_label}:{val:.1f}{extra}")


def on_review_image_hover(app, event) -> None:
    if not hasattr(app, "_review_image_label"):
        return
    pm = app._review_image_label.pixmap()
    if pm is not None and not pm.isNull():
        app._review_image_label._sz_w = int(pm.width())
        app._review_image_label._sz_h = int(pm.height())
    else:
        app._review_image_label._sz_w = int(app._review_image_label.width())
        app._review_image_label._sz_h = int(app._review_image_label.height())
    _show_image_pixel_tooltip(
        app,
        event,
        str(app._app_state.channel.active_image_channel or "").upper(),
        label=app._review_image_label,
    )


def on_review_image_channel_selected(app, _e=None) -> None:
    """Channel-switch handler that preserves Review Image zoom/pan view."""
    import logging
    from well_viewer.core import debug_flags as _debug_flags
    logger = logging.getLogger(__name__)
    if _debug_flags.review_image_channel_switch_debug_enabled():
        logger.debug("[RI-CHSW step 1] Review Image channel ComboboxSelected event received")
    selected_ui_value = ""
    if getattr(app, "_review_image_chan_cb", None) is not None:
        selected_ui_value = str(app._review_image_chan_cb.currentText() or "").strip()
    if not selected_ui_value and hasattr(app, "_review_image_chan_var"):
        selected_ui_value = app._review_image_chan_var.currentText()
    if _debug_flags.review_image_channel_switch_debug_enabled():
        logger.debug(
            "[RI-CHSW step 2] review_image_channel_selected ui_value=%r active_before=%r",
            selected_ui_value,
            app._app_state.channel.active_image_channel,
        )
    set_active_image_channel(app, selected_ui_value.lower(), preserve_review_view=True)







# ── Flat-field validation view (recompute the selected flat-field) ────────────











def pick_review_image_color(app, which: str) -> None:
    """Open a v2 ColorSwatchRow popover anchored at the swatch button."""
    from PySide6.QtGui import QColor

    from widgets.color_swatch_row import ColorSwatchRow
    from widgets.popover import Popover

    anchor = (getattr(app, "_review_image_color_swatches", {}) or {}).get(which)
    if anchor is None:
        return

    r, g, b = app._review_image_get_color(which)
    recents = list(getattr(app, "_review_image_color_recents", []) or [])

    pop = Popover(anchor)
    row = ColorSwatchRow(allow_custom=True, recents=recents)
    row.setCurrentColor(QColor(int(r), int(g), int(b)))

    def _apply(c: QColor, _which=which, _pop=pop, _row=row) -> None:
        if not c.isValid():
            return
        app._review_image_set_color(_which, (c.red(), c.green(), c.blue()))
        rec = [c] + [QColor(x) for x in (getattr(app, "_review_image_color_recents", []) or [])
                     if QColor(x).rgb() != c.rgb()]
        app._review_image_color_recents = rec[:8]

    row.colorPicked.connect(_apply)
    pop.setContentWidget(row)
    pop.popup(anchor, side="bottom", align="start")











def set_review_cell_included(app, fov: str, tp: str, nid: str, included: str) -> None:
    if app._app_state.preview.selected_well is None:
        return
    fov_n, tp_n, nid_n = app._review_row_keys({"fov": fov, "tp": tp, "nucleus_id": nid})
    if not (fov_n and tp_n and nid_n):
        return
    key = (app._app_state.preview.selected_well, fov_n, tp_n, nid_n)
    app._review_included_overrides[key] = str(included).strip() or "1"
    # Project the override onto the cached row immediately so other tabs
    # (Bar/Line/Distribution/Stats/Heatmap) honor it without waiting for
    # the next gating recompute, then invalidate the stats cache.
    apply_review_overrides_to_cache(app,app._app_state.preview.selected_well)
    if hasattr(app, "_invalidate_stats_cache"):
        try:
            app._invalidate_stats_cache()
        except Exception:
            swallowed()  # was: pass (silent except)
    # Persist to disk (debounced) so curation survives between sessions.
    app._persistence.schedule_save("cell_overrides")
    # Bump the override version so the include-map cache is bypassed
    # on the next refresh; the frame cache (decoded image + boundary)
    # remains valid because the underlying mask label image is unchanged.
    app._app_state.review_image.override_version += 1
    prev_zoom = float(app._app_state.review_image.zoom)
    prev_pan_x = float(app._app_state.review_image.pan_x)
    prev_pan_y = float(app._app_state.review_image.pan_y)
    # Skip a full Review CSV table rebuild here — the user is on the
    # Review Image tab and rebuilding tens of thousands of QTableWidgetItems
    # per click was the dominant memory leak. The table reads the effective
    # Included via override on the next refresh (combobox change / Refresh
    # button / well-selection change), so the value is never lost.
    refresh_review_image(app)
    app._app_state.review_image.zoom = prev_zoom
    app._app_state.review_image.pan_x = prev_pan_x
    app._app_state.review_image.pan_y = prev_pan_y
    app._render_review_image_display()



def set_review_cells_included_batch(
    app, items: List[Tuple[str, str, str, str]]
) -> None:
    """Apply many (fov, tp, nid, included) overrides with a single redraw.

    Mirrors _set_review_cell_included but coalesces the per-call
    cache-projection, stats invalidation, save-schedule, and image
    refresh — otherwise a 100-cell box delete would redraw 100 times.
    """
    if app._app_state.preview.selected_well is None or not items:
        return
    well = app._app_state.preview.selected_well
    any_changed = False
    for fov, tp, nid, included in items:
        fov_n, tp_n, nid_n = app._review_row_keys(
            {"fov": fov, "tp": tp, "nucleus_id": nid}
        )
        if not (fov_n and tp_n and nid_n):
            continue
        app._review_included_overrides[(well, fov_n, tp_n, nid_n)] = (
            str(included).strip() or "1"
        )
        any_changed = True
    if not any_changed:
        return
    apply_review_overrides_to_cache(app,well)
    if hasattr(app, "_invalidate_stats_cache"):
        try:
            app._invalidate_stats_cache()
        except Exception:
            swallowed()  # was: pass (silent except)
    app._persistence.schedule_save("cell_overrides")
    app._app_state.review_image.override_version += 1
    prev_zoom = float(app._app_state.review_image.zoom)
    prev_pan_x = float(app._app_state.review_image.pan_x)
    prev_pan_y = float(app._app_state.review_image.pan_y)
    refresh_review_image(app)
    app._app_state.review_image.zoom = prev_zoom
    app._app_state.review_image.pan_x = prev_pan_x
    app._app_state.review_image.pan_y = prev_pan_y
    app._render_review_image_display()



def apply_box_delete(app, x0: int, y0: int, x1: int, y1: int) -> None:
    """Mark every cell with any pixel in mask[y0:y1, x0:x1] as Included=0.

    Coordinates are in mask-array pixel space (already converted from
    label-local pixels by _on_review_image_release using the current
    _review_image_scale, so window position, scroll, and zoom are
    already accounted for).
    """
    label = getattr(app, "_review_image_label", None)
    mask = getattr(label, "_mask_arr", None) if label is not None else None
    if mask is None or app._app_state.preview.selected_well is None:
        return
    h, w = int(mask.shape[0]), int(mask.shape[1])
    xa, xb = sorted((max(0, min(w, int(x0))), max(0, min(w, int(x1)))))
    ya, yb = sorted((max(0, min(h, int(y0))), max(0, min(h, int(y1)))))
    if xb - xa < 1 or yb - ya < 1:
        return
    sub = mask[ya:yb, xa:xb]
    nids = [int(n) for n in _np.unique(sub) if int(n) > 0]
    if not nids:
        app._set_status("Box selection: no cells inside rectangle.")
        return
    fov_cb = getattr(app, "_review_image_fov_menu", None) or getattr(app, "_preview_fov_cb", None)
    if fov_cb is None:
        return
    fov = fov_cb.currentText().strip()
    tp_cb = getattr(app, "_review_image_tp_cb", None)
    tp = tp_cb.currentText().strip() if tp_cb is not None else ""
    set_review_cells_included_batch(app,
        [(fov, tp, str(nid), "0") for nid in nids]
    )
    app._set_status(
        f"Box delete: marked {len(nids)} cell(s) Included=0."
    )



def zoom_review_image_to_selected_nucleus(app, zoom: float = 3.0) -> None:
    if not hasattr(app, "_review_image_label") or not hasattr(app, "_review_image_canvas"):
        return
    nid = app._app_state.review_image.selected_nucleus
    mask_arr = getattr(app._review_image_label, "_mask_arr", None)
    if nid is None or mask_arr is None:
        return
    ys, xs = _np.where(mask_arr == int(nid))
    if len(xs) == 0 or len(ys) == 0:
        return
    cx = float(xs.mean())
    cy = float(ys.mean())
    app._app_state.review_image.zoom = float(max(1.0, zoom))
    rgb = getattr(app, "_review_image_base_rgb", None)
    if rgb is not None:
        ih, iw = rgb.shape[:2]
    else:
        img = app._app_state.review_image.base_pil
        if img is None:
            return
        iw, ih = img.size
    vp = app._review_image_canvas.viewport()
    cw = max(1, vp.width())
    ch = max(1, vp.height())
    fit = min(cw / max(iw, 1), ch / max(ih, 1))
    scale = max(0.05, fit * max(0.1, float(app._app_state.review_image.zoom)))
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    # The renderer derives the scrollbar value as
    #     scroll = max(0, (max(nw, cw) - cw) // 2) - int(pan)
    # and the QLabel host's pixmap sits at x=0 inside it when the
    # scaled image exceeds the viewport (nw > cw), so the centring
    # formula has to be pan = nw/2 - cx*scale (and likewise for y).
    # The old `cw/2 - base_x - cx*scale` form mis-offset the pan by
    # roughly (nw - cw)/2 px once you zoomed in, which is why the
    # yellow nucleus ended up off-screen.
    app._app_state.review_image.pan_x = (nw / 2.0) - (cx * scale)
    app._app_state.review_image.pan_y = (nh / 2.0) - (cy * scale)
    app._render_review_image_display()






def refresh_review_csv(app) -> None:
    if not hasattr(app, "_review_csv_table"):
        return
    sels = sorted(app._app_state.selection.selected_wells, key=app._parse_rc)
    # When the user has no explicit picks but rep-sets exist, fall back
    # to every well in the active groups so the table populates on first
    # visit. Previously this path took the "Select one or more wells"
    # branch and left Review CSV empty whenever groups were defined.
    if not sels and app._app_state.selection.selections:
        seen: set = set()
        for rset in app._rep_sets_active():
            for w in rset.wells:
                if w in app._app_state.dataset.well_paths and w not in seen:
                    seen.add(w)
        sels = sorted(seen, key=app._parse_rc)
    if not sels:
        app._review_well_lbl.setText("(select well(s))")
        _set_combo_values(app._review_fov_cb, [])
        _set_combo_values(app._review_tp_cb, [])
        app._review_fov_cb.setCurrentText("")
        app._review_tp_cb.setCurrentText("")
        app._refresh_review_csv_rows([])
        app._review_csv_msg_lbl.setText("Select one or more wells in the picker.")
        return

    app._review_well_lbl.setText(
        ", ".join(sels[:3]) + (f" (+{len(sels) - 3} more)" if len(sels) > 3 else "")
    )
    rows: List[dict] = []
    for label in sels:
        rows.extend(app._review_load_rows(label))
    if not rows:
        _set_combo_values(app._review_fov_cb, [])
        _set_combo_values(app._review_tp_cb, [])
        app._review_fov_cb.setCurrentText("")
        app._review_tp_cb.setCurrentText("")
        app._refresh_review_csv_rows([])
        app._review_csv_msg_lbl.setText("No CSV rows loaded for selected well(s).")
        return

    # Normalize the FOV combo values the SAME way the cell-click navigation and
    # the row filter do (review_norm_fov / _review_row_keys). The combo is
    # non-editable, so if its items were the raw CSV strings ("1.0", "01") while
    # a click calls setCurrentText("1"), the select silently no-ops, the filter
    # lands on the wrong (or an empty) FOV, and refresh_review_csv_rows falls
    # back to materialising *every* loaded row — the OOM that SIGKILLs the app.
    def _fov_sort_key(s: str):
        try:
            return (0, float(s))
        except (TypeError, ValueError):
            return (1, s)
    fovs = sorted(
        {review_norm_fov(r.get("fov", r.get("FOV", "")))
         for r in rows
         if review_norm_fov(r.get("fov", r.get("FOV", "")))},
        key=_fov_sort_key,
    )
    tps: list[str] = []
    seen_tps: set[str] = set()
    for row in rows:
        raw_tp = str(row.get("timepoint", row.get("tp", row.get("time", "")))).strip()
        norm_tp = app._norm_timepoint(raw_tp)
        if not norm_tp or norm_tp in seen_tps:
            continue
        seen_tps.add(norm_tp)
        tps.append(norm_tp)
    tps.sort(key=lambda tp: (parse_timepoint_hours(tp) is None, parse_timepoint_hours(tp) or 0.0, tp))
    _set_combo_values(app._review_fov_cb, fovs)
    _set_combo_values(app._review_tp_cb, tps)
    if fovs and app._review_fov_cb.currentText() not in fovs:
        app._review_fov_cb.setCurrentText(fovs[0])
    if tps and app._review_tp_cb.currentText() not in tps:
        app._review_tp_cb.setCurrentText(tps[0])
    app._refresh_review_csv_rows(rows)



def refresh_review_csv_rows(app, rows: Optional[List[dict]] = None) -> None:
    if not hasattr(app, "_review_csv_table"):
        return
    table = app._review_csv_table
    table.setRowCount(0)

    if rows is None:
        sels = sorted(app._app_state.selection.selected_wells, key=app._parse_rc)
        rows = []
        for label in sels:
            rows.extend(app._review_load_rows(label))

    def _norm(v: object) -> str:
        s = str(v or "").strip()
        if not s:
            return ""
        try:
            return f"{float(s):g}"
        except Exception:
            return s

    fov_sel = _norm(app._review_fov_cb.currentText()) if hasattr(app, "_review_fov_cb") else ""
    tp_sel = app._norm_timepoint(app._review_tp_cb.currentText()) if hasattr(app, "_review_tp_cb") else ""
    filtered = []
    for row in rows:
        row_fov = _norm(row.get("fov", row.get("FOV", "")))
        row_tp = app._norm_timepoint(row.get("timepoint", row.get("tp", row.get("time", ""))))
        if fov_sel and row_fov != fov_sel:
            continue
        if tp_sel and row_tp != tp_sel:
            continue
        filtered.append(row)

    if not filtered:
        _logger.warning(
            "No Review CSV rows matched filters: selected_wells=%s fov=%s tp=%s total_rows=%d",
            sorted(app._app_state.selection.selected_wells, key=app._parse_rc),
            fov_sel, tp_sel, len(rows),
        )
        if not rows:
            table.setColumnCount(0)
            app._review_csv_msg_lbl.setText("No rows are available for the selected well(s).")
            return
        # Fallback: if the filters are mismatched for any reason, still
        # show all loaded rows from selected well(s) instead of an empty table.
        filtered = list(rows)
        app._review_csv_msg_lbl.setText(
            "No exact Well/FOV/Timepoint match. Showing all selected rows instead."
        )
        ctx = getattr(app, "_review_csv_lookup_context", {}) or {}
        ctx_txt = (
            f" well={ctx.get('well','')} fov={ctx.get('fov','')} tp={ctx.get('tp','')} nucleus_id={ctx.get('nucleus_id','')}"
            if ctx else ""
        )
        app._set_status(
            f"Review CSV fallback active: no exact match for FOV={fov_sel}, TP={tp_sel}; showing {len(filtered)} row(s).{ctx_txt}"
        )

    # Hard cap on materialised rows. A QTableWidget builds one heavyweight
    # QTableWidgetItem per cell, so an unbounded build (e.g. the all-rows
    # fallback above, or a multi-well selection spanning every FOV/timepoint)
    # can allocate millions of items and get the process OOM-killed. The table
    # is an inspection aid for a single FOV/timepoint (a few hundred cells), so
    # truncating past the cap costs nothing real while removing the crash.
    _MAX_TABLE_ROWS = 5000
    n_total = len(filtered)
    truncated = n_total > _MAX_TABLE_ROWS
    if truncated:
        filtered = filtered[:_MAX_TABLE_ROWS]

    cols = list(filtered[0].keys())
    table.setColumnCount(len(cols))
    table.setHorizontalHeaderLabels(cols)
    for ci in range(len(cols)):
        table.setColumnWidth(ci, 120)
    # Rows are now references into the per-well row caches (no deep copy),
    # so row['Included'] reflects the gating-computed value, not any user
    # override applied via the Review Image tab. Compute the effective
    # value at display time so the table mirrors what the image overlay
    # shows.
    try:
        included_col = cols.index("Included")
    except ValueError:
        included_col = -1
    for row in filtered:
        r = table.rowCount()
        table.insertRow(r)
        well_label = str(row.get("well", "") or "")
        for ci, c in enumerate(cols):
            if ci == included_col and well_label:
                value = app._review_effective_included(well_label, row)
            else:
                value = row.get(c, "")
            table.setItem(r, ci, QTableWidgetItem(str(value)))
    if truncated:
        app._review_csv_msg_lbl.setText(
            f"Showing first {len(filtered):,} of {n_total:,} row(s) "
            "— narrow the FOV / timepoint to see the rest."
        )
    else:
        app._review_csv_msg_lbl.setText(f"Showing {len(filtered):,} row(s).")
















