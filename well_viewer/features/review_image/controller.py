"""Controller helpers for Review Image <-> Review CSV interactions."""

from __future__ import annotations

from typing import List, Optional

import logging

from PySide6.QtWidgets import QTableWidgetItem

from well_viewer.features.review_image.renderer import refresh_review_image
from well_viewer.features.review_image.renderer import update_preview
from well_viewer.core.channel_state_controller import set_active_image_channel
from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values
from well_viewer.data.data_loading import parse_timepoint_hours
from well_viewer.data.image_resolver import resolve_ref_by_fov_tp as _resolve_ref_by_fov_tp
from well_viewer.core.ui_helpers import safe_int_or_none as _safe_int_or_none
import numpy as _np
import numpy as np
import pandas as pd

from well_viewer.data.data_loading import df_included_mask, resolve_value_series

_logger = logging.getLogger(__name__)



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
        apply_box_delete(app,mx0, my0, mx1, my1)
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
    from well_viewer.features.preview.montage import _show_image_pixel_tooltip
    _show_image_pixel_tooltip(
        app,
        event,
        f"{app._app_state.channel.active_image_channel.upper()}",
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


def review_row_keys_series(app, df: pd.DataFrame) -> Optional[pd.Series]:
    """Vectorized counterpart of ``_review_row_keys`` for a DataFrame.

    Returns a ``Series`` of ``(fov, tp, nid)`` tuples aligned with
    ``df.index``, or ``None`` if no usable identifier columns are present.

    Built on :func:`image_identity.cell_keys_for_dataframe` so this side
    of the lookup uses the same well/fov/tp/nid normalisation chain the
    smFISH writer and ``cell_overrides`` use — no more drift between the
    review-image overrides side (used to use ``:g`` float format) and the
    smFISH writer side (used to strip only)."""
    if df is None or df.empty:
        return None
    from well_viewer.data.image_identity import cell_keys_for_dataframe
    keys = cell_keys_for_dataframe(df)
    if keys.empty:
        return None
    # Strip the well token — historical callers want a 3-tuple
    # (fov, tp, nid) keyed off the active well selection separately.
    return pd.Series([(k.fov, k.tp, k.nid) for k in keys], index=df.index)



def review_resolve_image_refs(app, *, fov_raw: str, tp_raw: str):
    """Resolve (fluor_ref, mask_ref).

    Honors ``_review_image_show_raw``: when True, prefer the unprocessed
    fluorescence channel and fall back to the top-hat-filtered output if
    the raw frame is unavailable; when False (default), prefer top-hat
    and fall back to raw — the original behaviour. Falling back rather
    than failing keeps Review Image usable on datasets that only ship
    one of the two image kinds for a given FOV/timepoint.
    """
    raw_first = bool(app._app_state.review_image.show_raw)
    tophat_map = getattr(app, "_preview_tophat_fluor", {})
    raw_map = app._app_state.preview.fluor
    if raw_first:
        primary, fallback = raw_map, tophat_map
    else:
        primary, fallback = tophat_map, raw_map
    fluor_ref = _resolve_ref_by_fov_tp(
        primary, fov_raw=fov_raw, tp_raw=tp_raw,
        norm_timepoint=app._norm_timepoint,
    )
    if fluor_ref is None:
        fluor_ref = _resolve_ref_by_fov_tp(
            fallback, fov_raw=fov_raw, tp_raw=tp_raw,
            norm_timepoint=app._norm_timepoint,
        )
    mask_ref = _resolve_ref_by_fov_tp(
        app._app_state.preview.mask, fov_raw=fov_raw, tp_raw=tp_raw,
        norm_timepoint=app._norm_timepoint,
    )
    return fluor_ref, mask_ref



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



def review_build_include_map(
    app, mask_arr, well: str, fov: str, tp: str,
) -> Dict[int, bool]:
    """Build {nid: is_included} for all labels in ``mask_arr``."""
    center = _np.asarray(mask_arr)
    include_by_nid: Dict[int, bool] = {
        int(nid): True for nid in _np.unique(center) if int(nid) > 0
    }
    df = app._get_rows(well)
    if df is not None and not df.empty:
        keys = review_row_keys_series(app,df)
        if keys is not None:
            target = (fov, tp, None)
            fov_eq = keys.map(lambda k: k[0] == fov and k[1] == tp and k[2] != "")
            sub = df.loc[fov_eq.fillna(False).to_numpy()]
            if not sub.empty:
                sub_keys = keys.loc[sub.index]
                nids = sub_keys.map(lambda k: k[2]).map(_safe_int_or_none)
                incl = sub["Included"].map(lambda v: int(float(str(v).strip() or "1")) if str(v).strip() not in ("",) else 1)
                overrides = app._review_included_overrides
                for nid, base_incl in zip(nids, incl):
                    if nid is None:
                        continue
                    ovr = overrides.get((well, fov, tp, str(nid)))
                    if ovr is not None:
                        include_by_nid[nid] = (str(ovr).strip() != "0")
                    elif nid in include_by_nid:
                        include_by_nid[nid] = bool(base_incl)
    # Apply overrides for cells present in the mask but without a CSV row
    # (e.g. when no pipeline results are loaded yet).
    for (ovr_well, ovr_fov, ovr_tp, ovr_nid), val in app._review_included_overrides.items():
        if ovr_well != well or ovr_fov != fov or ovr_tp != tp:
            continue
        nid = _safe_int_or_none(ovr_nid)
        if nid is None:
            continue
        if nid in include_by_nid:
            include_by_nid[nid] = (val.strip() != "0")
    return include_by_nid



def review_build_threshold_map(
    app, mask_arr, well: str, fov: str, tp: str,
) -> Dict[int, bool]:
    """Build {nid: above_threshold} for cells in the displayed FOV/timepoint.

    Mirrors the bar plot's gating logic: a cell is "above threshold" only
    when it passes (a) cell-area gating, (b) every fluorescence gate, and
    (c) ``row[active_val_col] > thresh_frac_on``.
    """
    center = _np.asarray(mask_arr)
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    threshold = app._get_thresh_frac_on()
    val_col = app._app_state.channel.active_val_col
    ratios = app._app_state.channel.ratio_index
    above_by_nid: Dict[int, bool] = {
        int(nid): False for nid in _np.unique(center) if int(nid) > 0
    }
    df = app._get_rows(well)
    if df is None or df.empty:
        return above_by_nid
    keys = review_row_keys_series(app,df)
    if keys is None:
        return above_by_nid
    sel = keys.map(lambda k: k[0] == fov and k[1] == tp and k[2] != "").fillna(False).to_numpy()
    sub = df.loc[sel]
    if sub.empty:
        return above_by_nid

    mask = df_included_mask(sub).to_numpy(copy=True)
    if "area_px" in sub.columns:
        area = pd.to_numeric(sub["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(area) & (area > cell_area_threshold)
    for chan, gate in fluor_gates.items():
        col = f"{chan}_mean_intensity"
        if col not in sub.columns:
            mask[:] = False
            break
        v = pd.to_numeric(sub[col], errors="coerce").to_numpy()
        mask &= np.isfinite(v) & (v > gate)
    val = resolve_value_series(sub, val_col, ratios).to_numpy()
    mask &= np.isfinite(val) & (val > threshold)

    sub_keys = keys.loc[sub.index]
    for k, ok in zip(sub_keys, mask):
        nid = _safe_int_or_none(k[2])
        if nid is not None and nid in above_by_nid:
            above_by_nid[nid] = bool(ok)
    return above_by_nid



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
            pass
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
            pass
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



def apply_review_overrides_to_cache(app, label: Optional[str] = None) -> None:
    """Project overrides onto cached row['Included'] so non-Review tabs see them.

    The override dict is the source of truth; this projects values onto the
    cached rows so every controller using row_is_included(row) automatically
    respects user toggles. Called after gating recomputes (which rewrite
    row['Included'] from thresholds), after patch-file load, and on toggle.
    """
    if not app._review_included_overrides:
        return
    by_label: Dict[str, Dict[Tuple[str, str, str], str]] = {}
    for (lbl, fov, tp, nid), val in app._review_included_overrides.items():
        if label is not None and lbl != label:
            continue
        by_label.setdefault(lbl, {})[(fov, tp, nid)] = val
    for lbl, lookup in by_label.items():
        if lbl not in app._app_state.dataset.well_paths:
            continue
        try:
            df = app._get_rows(lbl)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        keys = review_row_keys_series(app,df)
        if keys is None:
            continue
        override_map = pd.Series(lookup)
        override_for_row = keys.map(override_map)
        mask = override_for_row.notna()
        if not mask.any():
            continue
        def _coerce(val: object) -> int:
            try:
                return int(float(str(val).strip() or "1"))
            except (TypeError, ValueError):
                return 1 if str(val).strip() == "1" else 0
        df.loc[mask, "Included"] = override_for_row[mask].map(_coerce).astype(int)



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

    fovs = sorted({
        str(r.get("fov", r.get("FOV", ""))).strip()
        for r in rows
        if str(r.get("fov", r.get("FOV", ""))).strip()
    })
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
    app._review_csv_msg_lbl.setText(f"Showing {len(filtered):,} row(s).")



def review_row_keys(app, row: dict) -> Tuple[str, str, str]:
    def _norm(v: object) -> str:
        s = str(v or "").strip()
        if not s:
            return ""
        try:
            return f"{float(s):g}"
        except Exception:
            return s
    def _pick(*names: str) -> object:
        for name in names:
            if name in row:
                return row.get(name, "")
        lowered = {str(k).lower(): v for k, v in row.items()}
        for name in names:
            key = name.lower()
            if key in lowered:
                return lowered[key]
        return ""
    return (
        _norm(_pick("fov", "FOV")),
        app._norm_timepoint(_pick("timepoint", "tp", "time", "time_h", "timepoint_hours")),
        _norm(_pick("nucleus_id", "nucleus id", "nucleusId", "nucleusID")),
    )



def review_collect_timepoints(app, fov: str) -> list:
    """Sorted list of timepoints available for ``fov`` (fluor + pipeline)."""
    _norm = app._review_norm_fov
    tp_values = sorted(
        {
            app._norm_timepoint(tp)
            for (f, tp) in app._app_state.preview.fluor.keys()
            if _norm(f) == fov and app._norm_timepoint(tp)
        },
        key=app._review_tp_sort_key,
    )
    pipeline_tp_values = sorted(
        {
            app._norm_timepoint(tp)
            for tp in (app._app_state.dataset.pipeline_info or {}).get("available_timepoints", [])
            if app._norm_timepoint(tp)
        },
        key=app._review_tp_sort_key,
    )
    if pipeline_tp_values:
        tp_values = sorted(set(tp_values) | set(pipeline_tp_values), key=app._review_tp_sort_key)
    return tp_values



def review_load_rows(app, label: str) -> List[dict]:
    """Materialize the cached DataFrame for ``label`` as a list of row dicts.

    Used by the Review CSV table widget and the Review Image overlay
    builders. Mutations to returned dicts are local — write back to the
    canonical store via ``_apply_review_overrides_to_cache`` (which
    updates the cached DataFrame's ``Included`` column directly).
    """
    try:
        df = app._get_rows(label)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    return df.to_dict("records")



def review_effective_included(app, label: str, row: dict) -> str:
    """Effective Included value for a cached row.

    User overrides set via the Review Image tab take precedence over the
    gating-computed ``Included`` field stored on the cached row. Overrides
    are kept in a separate dict so they survive subsequent gating recomputes
    (which rewrite the canonical row['Included']).
    """
    fov, tp, nid = app._review_row_keys(row)
    val = None
    if fov and tp and nid:
        ovr = app._review_included_overrides.get((label, fov, tp, nid))
        if ovr is not None:
            val = ovr
    if val is None:
        val = str(row.get("Included", "1"))
    # Compose the QC layer: a QC-failed cell (QC_Pass == 0) is excluded
    # regardless of manual curation — effective = manual-keep AND passes-QC.
    qc = row.get("QC_Pass", 1)
    try:
        if int(float(str(qc).strip() or "1")) == 0:
            return "0"
    except (TypeError, ValueError):
        pass
    return val



def review_norm_fov(v: object) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        return f"{float(s):g}"
    except Exception:
        return s

