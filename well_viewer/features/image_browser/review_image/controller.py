"""Controller helpers for Review Image <-> Review CSV interactions."""

from __future__ import annotations

from typing import Any


import logging

from PySide6.QtWidgets import QTableWidgetItem

from well_viewer.features.image_browser.review_image.renderer import refresh_review_image_ctx
from well_viewer.features.image_browser.review_image.renderer import render_review_image_display
from well_viewer.features.image_browser.review_image.renderer import update_preview_ctx
from well_viewer.features.image_browser.review_image.renderer import (
    review_image_get_color, review_image_set_color,
    zoom_review_image_to_selected_nucleus,
    zoom_review_image_to_selected_nucleus_ctx,  # noqa: F401  (re-export)
)
from well_viewer.features.image_browser.review_image.box_select import (
    box_select_begin, box_select_finish, box_select_update,
)
# What a finished drag *does* lives in callbacks.py; the handlers here decide
# only which gesture is in progress. The dependency runs one way.
from well_viewer.features.image_browser.review_image.callbacks import (  # noqa: F401
    apply_box_delete, commit_included_overrides, override_key,
)
from well_viewer.core.channel_state_controller import set_active_image_channel
from well_viewer.data.curation import apply_curation_to_cache
from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values
from well_viewer.core.ui_helpers import norm_timepoint
# The FOV / timepoint sort orders and the row fetch live in collect.py now.
from well_viewer.domain.plot_members import plot_replicate_sets
from well_viewer.domain.viewer_state import parse_rc
import numpy as _np

from core.safe import swallowed

# Pure data/query helpers live in review_image/collect.py (T4). The Qt event
# handlers below + external callers (load_controller, the tab views) import
# these from here, so re-export them.
from well_viewer.features.image_browser.review_image.collect import (  # noqa: F401
    _binary_gate_columns,
    _flatfield_qc,
    _load_arrays_from_refs,
    _split_background_wells,
    any_review_rows,
    flatfield_field_for,
    review_build_include_map,
    review_build_threshold_map,
    review_collect_timepoints,
    review_effective_included,
    review_filter_values,
    review_load_rows,
    review_norm_fov,
    review_pooled_rows,
    review_resolve_image_refs,
    review_row_keys,
    review_row_keys_series,
)

_logger = logging.getLogger(__name__)

#: Hard cap on the rows the Review CSV table materialises. Passed to
#: ``collect.review_pooled_rows`` as its ``limit``, so it bounds the row dicts
#: as they are fetched and not merely the widget built from them — see that
#: function for what the unbounded pool cost.
MAX_TABLE_ROWS = 5000


def _guard_event(fn):
    """Wrap a Qt mouse-event handler so an exception can't escape into the C++
    event dispatch — under PySide6 that aborts the whole app (the "clicking a
    cell in the Segmentation tab crashes" report). Log the traceback instead so
    the click is a no-op and the cause is captured."""
    import functools

    @functools.wraps(fn)
    def _wrapped(app, event, *args, **kwargs) -> Any:
        try:
            return fn(app, event, *args, **kwargs)
        except Exception:  # never let a click kill the app
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
        ctx.notify.set_status(f"Set Included=0 for nucleus {nid} at FOV {fov}, TP {tp}.")
        return
    ctx.state.review_image.selected_nucleus = nid
    ctx.state.review_image.preserve_view_on_refresh = True
    refresh_review_image_ctx(ctx)
    select_review_csv_row_for_cell(ctx, fov, tp, str(nid), logger)


def select_review_csv_row_for_cell(ctx, fov: str, tp: str, nucleus_id: str, logger) -> None:
    """Navigate the Review CSV tab to the row for the given cell.

    Wrapped end-to-end: any failure is logged + surfaced on the status line
    rather than escaping into the Qt event loop (which previously left the UI
    looking hung). The post-refresh row scan reads only the FOV / timepoint /
    nucleus columns instead of materialising a dict per row, so a fallback
    "show all rows" table doesn't make this O(rows × columns).
    """
    from well_viewer.core.selection_controller import set_selected_wells
    try:
        fov_n, tp_n, nucleus_n = review_row_keys(ctx, {"fov": fov, "tp": tp, "nucleus_id": nucleus_id})
        lookup = {
            "well": str(ctx.state.preview.selected_well or ""),
            "fov": str(fov_n),
            "tp": str(tp_n),
            "nucleus_id": str(nucleus_n),
        }
        ctx.runtime.review.csv_lookup_context = lookup
        ctx.notify.set_status(
            "Review CSV lookup request: "
            f"well={lookup['well']}  "
            f"fov={lookup['fov']}  "
            f"tp={lookup['tp']}  "
            f"nucleus_id={lookup['nucleus_id']}"
        )
        logger.info(
            "Review-image click -> Review CSV lookup: well=%s fov=%s tp=%s nucleus_id=%s",
            ctx.state.preview.selected_well, fov, tp, nucleus_id,
        )
        # Ensure the previewed well is in the sidebar selection so the tab-switch
        # refresh loads its CSV rows — Review Image's preview well is independent
        # of _selected_wells, and _refresh_review_csv reads only _selected_wells.
        preview_well = ctx.state.preview.selected_well
        if (
            preview_well
            and preview_well in ctx.state.dataset.well_paths
            and preview_well not in ctx.state.selection.selected_wells
        ):
            # Go through the single mutation helper so _prev_sel is
            # snapshotted properly. commit=False because we're about to
            # switch tabs anyway — the tab-change handler will trigger
            # the redraw.
            new_sel = set(ctx.state.selection.selected_wells) | {preview_well}
            set_selected_wells(ctx, new_sel, commit=False)
        # If the Review CSV tab is already built, pre-point its FOV/TP combos
        # at the target *before* switching tabs. The tab-switch fires
        # _refresh_review_csv, which would otherwise rebuild the table for
        # whatever FOV/TP happened to be selected (and, when none of those
        # rows survive the filter, fall back to inserting *every* loaded row —
        # tens of thousands of QTableWidgetItems — which is what made this
        # look hung).
        review_fov_cb = ctx.widgets.get_or_none("_review_fov_cb")
        review_tp_cb = ctx.widgets.get_or_none("_review_tp_cb")
        if review_fov_cb is not None and review_tp_cb is not None:
            for combo, value in ((review_fov_cb, fov_n), (review_tp_cb, tp_n)):
                combo.blockSignals(True)
                try:
                    combo.setCurrentText(value)
                finally:
                    combo.blockSignals(False)
        # Switch to the Review CSV tab — this lazily builds the tab (and its
        # combo boxes / table) if the user hasn't visited it yet.
        notebook = getattr(ctx.window, "_notebook", None)
        if notebook is not None:
            _select_tab_by_text(notebook, "Review CSV")
        review_csv_table = ctx.widgets.get_or_none("_review_csv_table")
        review_fov_cb = ctx.widgets.get_or_none("_review_fov_cb")
        review_tp_cb = ctx.widgets.get_or_none("_review_tp_cb")
        if review_csv_table is None or review_fov_cb is None:
            ctx.notify.set_status("Review CSV tab is not available yet.")
            return
        # Block the per-combo currentIndexChanged signals so we rebuild the
        # table once here instead of once per setCurrentText.
        review_fov_cb.blockSignals(True)
        review_tp_cb.blockSignals(True)
        try:
            review_fov_cb.setCurrentText(fov_n)
            review_tp_cb.setCurrentText(tp_n)
        finally:
            review_fov_cb.blockSignals(False)
            review_tp_cb.blockSignals(False)

        # Rebuild the table only when the visible row set actually changes
        # (different well selection / FOV / timepoint). ``refresh_review_csv_rows``
        # stamps the rendered (wells, fov, tp) signature and a {(fov,tp,nid):
        # row} index onto the app; repeated clicks within the same FOV+timepoint
        # reuse them, so navigation is an O(1) index lookup + selectRow instead of
        # rebuilding the whole QTableWidget and linearly rescanning it every click.
        sel_wells = tuple(sorted(ctx.state.selection.selected_wells, key=parse_rc))
        want_sig = (sel_wells, fov_n, tp_n)
        if (ctx.runtime.review.csv_render_sig != want_sig
                or not ctx.runtime.review.csv_row_index):
            refresh_review_csv_rows(ctx)

        table = ctx.widgets.get_or_none("_review_csv_table")
        # Match the clicked well first (the index spans all selected wells); fall
        # back to the well-agnostic key for older single-well behaviour.
        index = ctx.runtime.review.csv_row_index or {}
        well_n = str(ctx.state.preview.selected_well or "").strip().upper()
        row_idx = index.get((well_n, fov_n, tp_n, nucleus_n))
        if row_idx is None:
            row_idx = index.get((fov_n, tp_n, nucleus_n))
        if row_idx is not None:
            table.selectRow(row_idx)
            item = table.item(row_idx, 0)
            if item:
                table.scrollToItem(item)
            ctx.notify.set_status(f"Matched Review CSV row for nucleus {nucleus_n} at FOV {fov_n}, TP {tp_n}.")
            return
        logger.warning(
            "Review CSV exact row match not found. target=(%s,%s,%s) rows=%d",
            fov_n, tp_n, nucleus_n, table.rowCount(),
        )
        ctx.notify.set_status(
            f"No exact Review CSV row match for nucleus {nucleus_n} at FOV {fov_n}, TP {tp_n}; showing fallback rows."
        )
    except Exception:
        logger.exception("Review-image -> Review CSV navigation failed")
        try:
            ctx.notify.set_status("Could not navigate to the Review CSV row (see log for details).")
        except Exception:
            swallowed()  # was: pass (silent except)


def on_review_csv_row_double_click(ctx, item) -> None:
    """Called with a QTableWidgetItem from itemDoubleClicked signal.

    A8 D4: the Review CSV tab wires this with ``ctx``, and the navigation body
    now walks ``ctx`` directly (the review-image draw web is ctx-native).

    Wrapped end-to-end so a partially-built Segmentation tab (e.g. the
    user double-clicks before the preview panel has materialised) shows a
    status message instead of bubbling a NoneType AttributeError out of
    the Qt event loop.
    """
    try:
        _on_review_csv_row_double_click_impl(ctx, item)
    except Exception as exc:
        import logging
        logging.getLogger("well_viewer").exception(
            "Review-CSV row double-click failed",
        )
        try:
            ctx.notify.set_status(
                f"Could not navigate to Segmentation: {exc} (see log for details).",
            )
        except Exception:
            swallowed()  # was: pass (silent except)


def _on_review_csv_row_double_click_impl(ctx, item) -> None:
    # A8 D4: ctx-native — the CSV table comes from ``ctx.widgets``; selection /
    # dataset / preview / review-image reads map to ``ctx.state``; the preview
    # FOV + review-image TP combos are the (unregistered) Qt widgets reached via
    # ``ctx.window``; the redraw + zoom go through the ctx-native leaves.
    table = ctx.widgets.get_or_none("_review_csv_table")
    if table is None:
        return
    row_idx = item.row()
    row = _table_row_dict(table, row_idx)
    fov, tp, nid = review_row_keys(ctx, row)
    well_tok = str(row.get("well", "")).strip()
    key = well_tok if well_tok in ctx.state.dataset.well_paths else None
    if key is None and ctx.state.selection.selected_wells:
        key = sorted(ctx.state.selection.selected_wells, key=parse_rc)[0]
    if key is None:
        return

    if getattr(ctx.window, "_notebook", None) is not None:
        # Segmentation is now a sub-tab of the unified Image Browser; route the
        # Review-CSV jump through the rail entry + sub-tab selection.
        from well_viewer.features.image_browser.controller import show_image_browser_subtab
        show_image_browser_subtab(ctx, "Segmentation")

    # Set the target nucleus before any refresh so intermediate renders can
    # already draw the yellow highlight.
    if nid:
        try:
            ctx.state.review_image.selected_nucleus = int(float(nid))
        except Exception:
            ctx.state.review_image.selected_nucleus = None

    ctx.state.preview.selected_well = key
    update_preview_ctx(ctx, key)

    # Re-sync the Segmentation sidebar's well picker — the plate widget and its
    # "Selected: …" label. The sub-tab activation above already refreshed the
    # picker, but that ran before this well was chosen, so without a second pass
    # the jump lands on the right image with the sidebar still reading "No well
    # selected" and no well lit on the plate.
    refresh_picker = getattr(ctx.window, "_refresh_preview_picker", None)
    if callable(refresh_picker):
        refresh_picker()

    # _preview_fov_var drives refresh_review_image; force the target FOV
    # before repopulating the TP menu. The combo only exists once the
    # Segmentation tab's preview panel has been built — guard for the
    # double-click-before-build case so we don't crash.
    if fov:
        fov_cb = getattr(ctx.window, "_preview_fov_cb", None)
        if fov_cb is not None:
            fov_cb.setCurrentText(fov)
    refresh_review_image_ctx(ctx)

    # TP menu now holds the target FOV's timepoints. Selecting the TP here
    # triggers refresh_review_image via currentIndexChanged, which renders
    # the correct frame with the highlight already in place.
    if tp:
        tp_cb = ctx.runtime.review_image.tp_cb
        if tp_cb is not None:
            tp_cb.setCurrentText(tp)

    zoom_review_image_to_selected_nucleus(ctx)


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
                mfi_by_nid = ctx.state.review_image.mfi_by_nid or {}
                mfi = mfi_by_nid.get(nid)
                if mfi is not None:
                    extra += f"  MFI:{mfi:.1f}"
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
    from core import debug_flags as _debug_flags
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
    key = override_key(ctx.state.preview.selected_well, fov, tp, nid)
    if key is None:
        return
    ctx.runtime.review.included_overrides[key] = str(included).strip() or "1"
    # Project the override onto the cached row immediately so other tabs
    # (Bar/Line/Distribution/Stats/Heatmap) honor it without waiting for
    # the next gating recompute, then invalidate the stats cache.
    apply_curation_to_cache(ctx, ctx.state.preview.selected_well)
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


def refresh_review_csv(ctx) -> None:
    """Rebuild the Review CSV filter combos + table for the current selection.

    A8 teardown: takes ``ctx`` and walks it directly. The filter combos / well
    label / table / message widgets are all registered in ``ctx.widgets`` by the
    Review CSV tab builder; the selection + dataset reads map to ``ctx.state``
    and the domain leaves (``parse_rc`` / ``active_replicate_sets``); row
    materialisation goes through the ctx-native ``data_loading.get_rows``. The
    per-row table build (``refresh_review_csv_rows``) still bridges to the
    app-native nav-index store — see there."""
    _refresh_review_csv_impl(ctx)


def _refresh_review_csv_impl(ctx) -> None:
    w = ctx.widgets
    table = w.get_or_none("_review_csv_table")
    if table is None:
        return
    sels = sorted(ctx.state.selection.selected_wells, key=parse_rc)
    # When the user has no explicit picks but rep-sets exist, fall back
    # to every well in the active groups so the table populates on first
    # visit. Previously this path took the "Select one or more wells"
    # branch and left Review CSV empty whenever groups were defined.
    if not sels and ctx.state.selection.selections:
        seen: set = set()
        for rset in plot_replicate_sets(ctx):
            for well in rset.wells:
                if well in ctx.state.dataset.well_paths and well not in seen:
                    seen.add(well)
        sels = sorted(seen, key=parse_rc)
    well_lbl = w.get_or_none("_review_well_lbl")
    fov_cb = w.get_or_none("_review_fov_cb")
    tp_cb = w.get_or_none("_review_tp_cb")
    msg_lbl = w.get_or_none("_review_csv_msg_lbl")
    if not sels:
        well_lbl.setText("(select well(s))")
        _set_combo_values(fov_cb, [])
        _set_combo_values(tp_cb, [])
        fov_cb.setCurrentText("")
        tp_cb.setCurrentText("")
        refresh_review_csv_rows(ctx, [])
        # The empty-selection message is the tab's shared placeholder now, which
        # renders where the table would be rather than in an 11px line below it.
        # The label stays — it still carries the "wells selected but no rows"
        # case below — but printing the selection sentence here too would show
        # the user the same thing twice, once under the other.
        msg_lbl.setText("")
        return

    well_lbl.setText(
        ", ".join(sels[:3]) + (f" (+{len(sels) - 3} more)" if len(sels) > 3 else "")
    )
    # The combo values come from each frame's DISTINCT column values, not from
    # a pooled list of one dict per cell. A well has a handful of FOVs and
    # timepoints however many cells it holds, so this is a per-well constant
    # where the pooled build scaled with the plate — see
    # ``collect.review_pooled_rows`` for what that cost.
    fovs, tps = review_filter_values(ctx, sels)
    if not any_review_rows(ctx, sels):
        _set_combo_values(fov_cb, [])
        _set_combo_values(tp_cb, [])
        fov_cb.setCurrentText("")
        tp_cb.setCurrentText("")
        refresh_review_csv_rows(ctx, [])
        msg_lbl.setText("No CSV rows loaded for selected well(s).")
        return

    # Normalize the FOV combo values the SAME way the cell-click navigation and
    # the row filter do (review_norm_fov / _review_row_keys). The combo is
    # non-editable, so if its items were the raw CSV strings ("1.0", "01") while
    # a click calls setCurrentText("1"), the select silently no-ops, the filter
    # lands on the wrong (or an empty) FOV, and refresh_review_csv_rows falls
    # back to materialising *every* loaded row — the OOM that SIGKILLs the app.
    _set_combo_values(fov_cb, fovs)
    _set_combo_values(tp_cb, tps)
    if fovs and fov_cb.currentText() not in fovs:
        fov_cb.setCurrentText(fovs[0])
    if tps and tp_cb.currentText() not in tps:
        tp_cb.setCurrentText(tps[0])
    # ``None`` rather than a pooled list: the row builder below fetches only the
    # rows the combos actually select, and only as many as the table can show.
    refresh_review_csv_rows(ctx, None)


def refresh_review_csv_rows(ctx, rows: list[dict] | None = None) -> None:
    """Populate the Review CSV table from *rows* (or the current selection).

    A8 E4-review: fully ctx-native — the table + filter combos + message label
    come from ``ctx.widgets``; the selection reads map to ``ctx.state``; the
    nav-index + last-lookup scratch lives on ``ctx.runtime.review``; row
    materialisation + effective-inclusion + row-key derivation go through the
    ctx-native collect leaves."""
    _refresh_review_csv_rows_impl(ctx, rows)


def _refresh_review_csv_rows_impl(ctx, rows: list[dict] | None = None) -> None:
    w = ctx.widgets
    table = w.get_or_none("_review_csv_table")
    if table is None:
        return
    table.setRowCount(0)
    # Reset the navigation index + render signature; rebuilt below once the
    # table is populated (used by select_review_csv_row_for_cell for O(1) jumps).
    ctx.runtime.review.csv_row_index = {}
    ctx.runtime.review.csv_render_sig = None
    msg_lbl = w.get_or_none("_review_csv_msg_lbl")

    fov_cb = w.get_or_none("_review_fov_cb")
    tp_cb = w.get_or_none("_review_tp_cb")
    fov_sel = review_norm_fov(fov_cb.currentText()) if fov_cb is not None else ""
    tp_sel = norm_timepoint(ctx, tp_cb.currentText()) if tp_cb is not None else ""

    _MAX_TABLE_ROWS = MAX_TABLE_ROWS

    if rows is None:
        sels = sorted(ctx.state.selection.selected_wells, key=parse_rc)
        filtered, n_total = review_pooled_rows(
            ctx, sels, fov_sel=fov_sel, tp_sel=tp_sel, limit=_MAX_TABLE_ROWS)
        any_rows = bool(filtered) or any_review_rows(ctx, sels)
    else:
        # A caller-supplied list (the empty-selection branches, and tests).
        # Filter it in place; it is already materialised, so there is nothing
        # to bound here.
        filtered = [
            row for row in rows
            if (not fov_sel
                or review_norm_fov(row.get("fov", row.get("FOV", ""))) == fov_sel)
            and (not tp_sel
                 or norm_timepoint(
                     ctx, row.get("timepoint", row.get("tp", row.get("time", "")))
                 ) == tp_sel)
        ]
        n_total = len(filtered)
        any_rows = bool(rows)

    if not filtered:
        _logger.warning(
            "No Review CSV rows matched filters: selected_wells=%s fov=%s tp=%s",
            sorted(ctx.state.selection.selected_wells, key=parse_rc),
            fov_sel, tp_sel,
        )
        if not any_rows:
            table.setColumnCount(0)
            msg_lbl.setText("No rows are available for the selected well(s).")
            return
        # Fallback: if the filters are mismatched for any reason, still show
        # rows from the selected well(s) instead of an empty table — bounded
        # the same way, because this is the path that used to materialise the
        # entire plate.
        if rows is None:
            sels = sorted(ctx.state.selection.selected_wells, key=parse_rc)
            filtered, n_total = review_pooled_rows(ctx, sels, limit=_MAX_TABLE_ROWS)
        else:
            n_total = len(rows)
            filtered = list(rows[:_MAX_TABLE_ROWS])
        if not filtered:
            table.setColumnCount(0)
            msg_lbl.setText("No rows are available for the selected well(s).")
            return
        msg_lbl.setText(
            "No exact Well/FOV/Timepoint match. Showing all selected rows instead."
        )
        lookup = ctx.runtime.review.csv_lookup_context or {}
        ctx_txt = (
            f" well={lookup.get('well','')} fov={lookup.get('fov','')} tp={lookup.get('tp','')} nucleus_id={lookup.get('nucleus_id','')}"
            if lookup else ""
        )
        ctx.notify.set_status(
            f"Review CSV fallback active: no exact match for FOV={fov_sel}, TP={tp_sel}; showing {len(filtered)} of {n_total} row(s).{ctx_txt}"
        )

    truncated = n_total > len(filtered)

    cols = list(filtered[0].keys())
    table.setColumnCount(len(cols))
    table.setHorizontalHeaderLabels(cols)
    # Size each column so the *header label* never clips (a flat 120 px cut off
    # longer column names). Header-text width only — cheap, no per-row scan — so
    # the populate stays fast; the user can still drag-resize.
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(table.horizontalHeader().font())
    for ci, c in enumerate(cols):
        table.setColumnWidth(ci, max(120, fm.horizontalAdvance(str(c)) + 28))
    # Rows are now references into the per-well row caches (no deep copy),
    # so row['Included'] reflects the gating-computed value, not any user
    # override applied via the Review Image tab. Compute the effective
    # value at display time so the table mirrors what the image overlay
    # shows.
    try:
        included_col = cols.index("Included")
    except ValueError:
        included_col = -1
    # Build the (fov, tp, nid) -> row-index map while populating so navigation
    # clicks can jump straight to the row. Batch the populate with painting off
    # — a QTableWidget relays out on every insertRow otherwise.
    row_index: dict = {}
    table.setUpdatesEnabled(False)
    try:
        for row in filtered:
            r = table.rowCount()
            table.insertRow(r)
            well_label = str(row.get("well", "") or "")
            for ci, c in enumerate(cols):
                if ci == included_col and well_label:
                    value = review_effective_included(ctx, well_label, row)
                else:
                    value = row.get(c, "")
                table.setItem(r, ci, QTableWidgetItem(str(value)))
            rf, rt, rn = review_row_keys(ctx, row)
            # Key the navigation index by WELL too: the CSV table can span
            # several selected wells, and the same (fov, timepoint, nucleus_id)
            # exists in each — without the well a click resolved to whichever
            # well happened to be inserted first (wrong row / missed highlight).
            rw = str(row.get("well", "") or "").strip().upper()
            row_index.setdefault((rw, rf, rt, rn), r)
            row_index.setdefault((rf, rt, rn), r)  # well-agnostic fallback
    finally:
        table.setUpdatesEnabled(True)
    ctx.runtime.review.csv_row_index = row_index
    ctx.runtime.review.csv_render_sig = (
        tuple(sorted(ctx.state.selection.selected_wells, key=parse_rc)),
        fov_sel, tp_sel,
    )
    if truncated:
        msg_lbl.setText(
            f"Showing first {len(filtered):,} of {n_total:,} row(s) "
            "— narrow the FOV / timepoint to see the rest."
        )
    else:
        msg_lbl.setText(f"Showing {len(filtered):,} row(s).")

