"""Controller helpers for Review Image <-> Review CSV interactions."""

from __future__ import annotations
from well_viewer.review_image_renderer import refresh_review_image
from well_viewer.review_image_renderer import update_preview


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
        app._set_review_cell_included(fov, tp, str(nid), "0")
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
            pass


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
            pass


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
        # The Segmentation rail entry now hosts a nested stack with two
        # sub-pages: "Segmentation" (the review image) and "smFISH".
        # Switch the outer page first, then drive the inner stack to the
        # Segmentation sub-page so jumps from Review CSV always land on
        # the review image (not the smFISH side if that was last open).
        _select_tab_by_text(app._notebook, "Segmentation")
        cs_nb = getattr(app, "_cell_segmentation_notebook", None)
        if cs_nb is not None:
            setter = getattr(cs_nb, "setCurrentByName", None)
            if setter is not None:
                setter("Segmentation")

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
    if hasattr(app, "_refresh_review_image"):
        refresh_review_image(app)

    # TP menu now holds the target FOV's timepoints. Selecting the TP here
    # triggers _refresh_review_image via currentIndexChanged, which renders
    # the correct frame with the highlight already in place.
    if tp:
        tp_cb = getattr(app, "_review_image_tp_cb", None)
        if tp_cb is not None:
            tp_cb.setCurrentText(tp)

    if hasattr(app, "_zoom_review_image_to_selected_nucleus"):
        app._zoom_review_image_to_selected_nucleus()


def on_review_image_wheel(app, event) -> None:
    try:
        dy = int(event.angleDelta().y())
    except Exception:
        dy = 0
    direction = +1 if dy > 0 else -1 if dy < 0 else 0
    if direction:
        app._review_image_zoom_step(direction)


def on_review_image_press(app, event) -> None:
    # In include-edit mode, LMB drag draws a rubber-band rectangle for
    # bulk delete instead of panning. We record the anchor in label-local
    # pixels (event.position()) so it is invariant to window position
    # and scrollbar state.
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QRubberBand
    if app._app_state.review_image.include_edit_mode:
        pos = event.position()
        ax, ay = float(pos.x()), float(pos.y())
        app._app_state.review_image.box_anchor = (ax, ay)
        app._app_state.review_image.box_active = True
        label = getattr(app, "_review_image_label", None)
        if label is not None:
            rb = app._review_image_rubber_band
            if rb is None:
                rb = QRubberBand(QRubberBand.Rectangle, label)
                app._review_image_rubber_band = rb
            rb.setGeometry(QRect(int(ax), int(ay), 1, 1))
            rb.show()
        return
    app._app_state.review_image.dragging = True
    app._app_state.review_image.drag_moved = False
    gp = event.globalPosition().toPoint()
    app._app_state.review_image.drag_last_xy = (int(gp.x()), int(gp.y()))


def on_review_image_drag(app, event) -> None:
    # Rubber-band update path: stay anchored in label-local pixels.
    from PySide6.QtCore import QRect
    if app._app_state.review_image.box_active:
        anchor = app._app_state.review_image.box_anchor
        rb = app._review_image_rubber_band
        if anchor is None or rb is None:
            return
        ax, ay = anchor
        pos = event.position()
        cx, cy = float(pos.x()), float(pos.y())
        x0 = int(min(ax, cx))
        y0 = int(min(ay, cy))
        w = max(1, int(abs(cx - ax)))
        h = max(1, int(abs(cy - ay)))
        rb.setGeometry(QRect(x0, y0, w, h))
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


def on_review_image_release(app, event) -> None:
    # Rubber-band finalize path. A drag shorter than this many label-local
    # pixels in either axis falls through to the existing single-cell
    # click handler, so quick taps in edit mode still toggle one cell.
    _CLICK_PX_THRESH = 3
    if app._app_state.review_image.box_active:
        anchor = app._app_state.review_image.box_anchor
        rb = app._review_image_rubber_band
        app._app_state.review_image.box_active = False
        app._app_state.review_image.box_anchor = None
        if rb is not None:
            rb.hide()
        if anchor is None:
            return
        ax, ay = anchor
        pos = event.position()
        rx, ry = float(pos.x()), float(pos.y())
        if abs(rx - ax) < _CLICK_PX_THRESH and abs(ry - ay) < _CLICK_PX_THRESH:
            app._on_review_image_click(event)
            return
        scale = float(getattr(app, "_review_image_scale", 1.0) or 1.0)
        label = getattr(app, "_review_image_label", None)
        off_x, off_y = 0, 0
        if label is not None:
            pm = label.pixmap()
            if pm is not None and not pm.isNull():
                off_x = max(0, (label.width() - pm.width()) // 2)
                off_y = max(0, (label.height() - pm.height()) // 2)
        mx0, mx1 = int((ax - off_x) / scale), int((rx - off_x) / scale)
        my0, my1 = int((ay - off_y) / scale), int((ry - off_y) / scale)
        app._apply_box_delete(mx0, my0, mx1, my1)
        return
    was_dragging = app._app_state.review_image.dragging
    moved = app._app_state.review_image.drag_moved
    app._app_state.review_image.dragging = False
    app._app_state.review_image.drag_moved = False
    if was_dragging and not moved:
        app._on_review_image_click(event)


def on_review_image_move(app, event) -> None:
    """Unified mouseMove handler: drag when LMB held, otherwise hover."""
    from PySide6.QtCore import Qt
    if event.buttons() & Qt.LeftButton:
        on_review_image_drag(app, event)
    else:
        on_review_image_hover(app, event)


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
    from well_viewer.montage_controller import _show_image_pixel_tooltip
    _show_image_pixel_tooltip(
        app,
        event,
        f"{app._app_state.channel.active_image_channel.upper()}",
        label=app._review_image_label,
    )


def on_review_image_channel_selected(app, _e=None) -> None:
    """Channel-switch handler that preserves Review Image zoom/pan view."""
    import logging
    from well_viewer import debug_flags as _debug_flags
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
    app._set_active_image_channel(selected_ui_value.lower(), preserve_review_view=True)
