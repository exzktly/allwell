"""Cell Browser orchestration: run the filter, page in thumbnails, navigate.

Reaches the ctx via the ``ctx`` argument (never imports ``WellViewerApp``).
Image loading is delegated to ``worker.CellImageLoader`` so the UI never blocks;
this module owns the page bookkeeping and turns worker results into grid tiles.
"""

from __future__ import annotations

from well_viewer.features.image_browser.review_image.collect import review_row_keys
from well_viewer.core.ui_helpers import norm_timepoint

import logging
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from well_viewer.core.safe import swallowed
from well_viewer.features.image_browser.cell_browser.collect import (
    Criterion,
    SortKey,
    collect_cells,
)
from well_viewer.features.image_browser.cell_browser.state import MAX_TOTAL, THUMB_PX
from well_viewer.features.image_browser.review_image.controller import (
    zoom_review_image_to_selected_nucleus,
)
from well_viewer.features.image_browser.review_image.renderer import refresh_review_image, update_preview

_logger = logging.getLogger("well_viewer")

GRID_COLS = 6


class _CellThumb(QLabel):
    """A clickable cell thumbnail — click highlights the cell in Segmentation."""

    def __init__(self, ctx, desc, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._desc = desc
        self.setFixedSize(THUMB_PX, THUMB_PX)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("CellThumb")
        self.setStyleSheet(
            "#CellThumb { background: #111; border: 1px solid #333; }"
            "#CellThumb:hover { border: 1px solid #e2a23a; }"
        )

    def mousePressEvent(self, ev) -> None:  # noqa: N802 (Qt override)
        on_cell_clicked(self._ctx, self._desc)
        super().mousePressEvent(ev)


def make_cell_thumb(ctx, rgb, desc) -> QWidget:
    """Build one grid tile for a composed cell (placeholder when rgb is None)."""
    thumb = _CellThumb(ctx, desc)
    tp = f"t={desc.timepoint_h:g}h" if desc.timepoint_h == desc.timepoint_h else "t=?"
    thumb.setToolTip(
        f"{desc.well}  FOV {desc.fov}  {tp}\nnucleus {desc.nucleus_id}")
    if rgb is None:
        thumb.setText("—")
    else:
        pm = rgb_to_pixmap(rgb).scaled(
            THUMB_PX - 2, THUMB_PX - 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        thumb.setPixmap(pm)
    # N9: caption the crop with the active-channel spot count when present.
    spot = getattr(desc, "spot_count", None)
    if spot is not None:
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(thumb, 0, Qt.AlignHCenter)
        cap = QLabel(f"{spot} spot" + ("" if spot == 1 else "s"), container)
        cap.setObjectName("Muted")
        cap.setAlignment(Qt.AlignCenter)
        col.addWidget(cap, 0, Qt.AlignHCenter)
        return container
    return thumb


# ── reading the filter/display widgets ───────────────────────────────────────

def read_criteria(ctx) -> List[Criterion]:
    out: List[Criterion] = []
    for combo, lo_edit, hi_edit in (ctx.runtime.cell_browser.criteria_rows or []):
        col = combo.currentData()
        if not col:
            continue
        out.append(Criterion(col, _num(lo_edit.text()), _num(hi_edit.text())))
    return out


def read_sort_keys(ctx) -> List[SortKey]:
    """Ordered sort keys from the sidebar's sort rows (first row = primary)."""
    out: List[SortKey] = []
    for combo, dir_btn in (ctx.runtime.cell_browser.sort_rows or []):
        field = combo.currentData()
        if not field:
            continue
        # dir_btn checked = descending.
        out.append(SortKey(field, ascending=not dir_btn.isChecked()))
    return out


def read_timepoints(ctx):
    """Checked timepoints (strict include-list). Returns ``None`` when the
    dataset has no timepoint dimension (nothing to filter), else the list of
    checked timepoints — possibly ``[]`` (none checked → no cells)."""
    sel = getattr(ctx.runtime.cell_browser, "tp_selections", {}) or {}
    if not sel:
        return None  # no timepoint dimension → don't filter on it
    out: List[float] = []
    for tp_str, on in sel.items():
        if on:
            try:
                out.append(float(tp_str))
            except (TypeError, ValueError):
                swallowed()
    return out


def read_fovs(ctx):
    """Checked FOVs (strict include-list); ``None`` when there's no FOV
    dimension, else the checked list (possibly ``[]`` → no cells)."""
    sel = getattr(ctx.runtime.cell_browser, "fov_selections", {}) or {}
    if not sel:
        return None
    return [f for f, on in sel.items() if on]


def read_channels(ctx) -> List[str]:
    chips = getattr(ctx.runtime.cell_browser, "channel_chips", {}) or {}
    return [tok for tok, cb in chips.items() if cb.isChecked()]


def _num(text: str):
    s = str(text).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ── run / page ───────────────────────────────────────────────────────────────

def apply_filters(ctx, *, live_sync: bool = False) -> None:
    """Collect the filtered cell set and load the first page of thumbnails.

    ``live_sync`` marks the automatic Split-View / gate re-collect paths (vs the
    explicit Generate button): on those, an empty timepoint/FOV include-list
    relaxes to "all" so a selection change isn't met with a silently-blank
    gallery (see ``collect_cells``)."""
    st = ctx.runtime.cell_browser.state
    st.criteria = read_criteria(ctx)
    st.timepoints = read_timepoints(ctx)
    st.fovs = read_fovs(ctx)
    st.sort_keys = read_sort_keys(ctx)
    st.channels_on = read_channels(ctx)
    bcb = getattr(ctx.runtime.cell_browser, "boundary_cb", None)
    st.show_boundaries = bool(bcb.isChecked()) if bcb is not None else True
    src = getattr(ctx.runtime.cell_browser, "source_seg", None)
    st.use_tophat = (src.currentData() == "corrected") if src is not None else False
    lcb = getattr(ctx.runtime.cell_browser, "label_spot_cb", None)
    st.label_spot_count = bool(lcb.isChecked()) if lcb is not None else False
    gcb = getattr(ctx.runtime.cell_browser, "gate_mode_cb", None)
    st.scatter_gate_mode = bool(gcb.isChecked()) if gcb is not None else False
    st.page_size = _page_size(ctx)

    # Sort happens inside collect_cells (before the MAX_TOTAL cap, so the cap
    # keeps the top-ordered cells).
    cells = collect_cells(ctx, st.criteria, st.timepoints, st.fovs, st.sort_keys,
                          live_sync=live_sync)
    if len(cells) > MAX_TOTAL:
        cells = cells[:MAX_TOTAL]
    st.cells = cells
    st.loaded = 0
    ctx.runtime.cell_browser.gen = (ctx.runtime.cell_browser.gen or 0) + 1
    _clear_grid(ctx)
    _update_count(ctx)
    if not st.channels_on:
        _set_status(ctx, "Select at least one channel to display.")
        return
    if not cells:
        # The timepoint/FOV pickers start fully deselected (a deliberate
        # include-list default), so the commonest reason for an empty gallery is
        # "nothing picked yet". Name the exact next click rather than leaving a
        # blank grid with no explanation.
        _set_status(ctx, _empty_gallery_hint(st))
        _set_apply_busy(ctx, False)
        return
    _set_apply_busy(ctx, True)
    load_next_page(ctx)


def load_next_page(ctx) -> None:
    st = ctx.runtime.cell_browser.state
    if st.remaining <= 0:
        _update_count(ctx)
        return
    page = st.cells[st.loaded:st.loaded + st.page_size]
    start = st.loaded
    loader = ctx.runtime.cell_browser.loader
    loader.load_page(page, start, list(st.channels_on),
                     st.show_boundaries, ctx.runtime.cell_browser.gen,
                     use_tophat=st.use_tophat)
    # Optimistically advance; on_page_finished reconciles the count.
    st.loaded += len(page)
    _update_count(ctx)
    btn = getattr(ctx.runtime.cell_browser, "load_more_btn", None)
    if btn is not None:
        btn.setEnabled(False)  # re-enabled when the page finishes


def on_cell_ready(ctx, payload: dict) -> None:
    """Queued-signal sink: add one composed thumbnail to the grid."""
    if payload.get("gen") != (ctx.runtime.cell_browser.gen or 0):
        return  # superseded by a newer filter run
    idx = int(payload["index"])
    rgb = payload.get("rgb")
    desc = payload["desc"]
    tile = make_cell_thumb(ctx, rgb, desc)
    grid = getattr(ctx.runtime.cell_browser, "grid", None)
    if grid is None:
        return
    grid.addWidget(tile, idx // GRID_COLS, idx % GRID_COLS)


def on_page_finished(ctx, payload: dict) -> None:
    if payload.get("gen") != (ctx.runtime.cell_browser.gen or 0):
        return
    _update_count(ctx)
    btn = getattr(ctx.runtime.cell_browser, "load_more_btn", None)
    if btn is not None:
        btn.setEnabled(ctx.runtime.cell_browser.state.remaining > 0)
    _set_apply_busy(ctx, False)


def _empty_gallery_hint(st) -> str:
    """Name the most likely reason the filtered set is empty (and the fix)."""
    if getattr(st, "scatter_gate_mode", False):
        return ("No cells in the scatter gate — draw or adjust a gate on the "
                "Scatter Plot, or turn off “From scatter gate”.")
    if st.timepoints == []:
        return "No timepoints selected — click Timepoints to choose which to show."
    if st.fovs == []:
        return "No FOVs selected — click FOVs to choose which to show."
    return "No cells match the current filters — relax the criteria above."


def cell_browser_sync_scatter_gates(ctx) -> None:
    """Re-collect the gallery from the active scatter gate(s) when gate-source
    mode is on and the Cell Browser is visible — the live half of the
    Scatter ⇄ Cell Browser brushing (N1). Wired from the scatter gating module's
    ``_notify_gates_changed`` via ``ctx.runtime.cell_browser.gates_changed_cb``; a no-op
    when the mode is off or the tab isn't on screen (so it costs nothing in the
    common case)."""
    st = getattr(ctx.runtime.cell_browser, "state", None)
    if st is None or not getattr(st, "scatter_gate_mode", False):
        return
    from well_viewer.core.tab_controller import is_tab_visible
    if not is_tab_visible(ctx, "Cell Browser"):
        return
    apply_filters(ctx, live_sync=True)


def _set_apply_busy(ctx, busy: bool) -> None:
    """Disable the Apply button + signal progress while the worker composes a
    page, so a click gives immediate feedback instead of a frozen-looking grid."""
    btn = getattr(ctx.runtime.cell_browser, "apply_btn", None)
    if btn is not None:
        btn.setEnabled(not busy)
        btn.setText("Loading…" if busy else "Apply filters")
    gen_btn = getattr(ctx.runtime.cell_browser, "generate_btn", None)
    if gen_btn is not None:
        gen_btn.setEnabled(not busy)
        gen_btn.setText("Loading…" if busy else "Generate")
    if busy:
        _set_status(ctx, "Loading cells…")


def on_cell_clicked(ctx, desc) -> None:
    """Highlight the clicked cell in the Segmentation view (mirrors the
    Review-CSV jump-to-image navigation)."""
    try:
        if getattr(ctx.window, "_notebook", None) is not None:
            from well_viewer.features.image_browser.controller import (
                show_image_browser_subtab,
            )
            show_image_browser_subtab(ctx.app, "Segmentation")
        # Target nucleus before any refresh so intermediate renders already
        # draw the highlight (mirrors review_image.controller's jump-to-cell).
        if desc.nucleus_id:
            try:
                ctx.state.review_image.selected_nucleus = int(float(desc.nucleus_id))
            except (TypeError, ValueError):
                ctx.state.review_image.selected_nucleus = None
        # Switch wells *through* update_preview so the target well's image maps
        # are decoded and the FOV menu is repopulated — otherwise jumping to a
        # cell in a different well leaves the FOV combo (and image) stale.
        ctx.state.preview.selected_well = desc.well
        update_preview(ctx.app, desc.well)
        # The FOV / timepoint combos hold *normalized* tokens — FOV as
        # ``float:g`` (renderer ``_norm_fov``) and timepoint as parsed hours
        # ``:g`` (``_norm_timepoint``). The descriptor carries the raw CSV
        # tokens, so passing them straight to setCurrentText silently no-ops
        # (no matching item) and the view stays on the previous FOV/timepoint.
        # Normalize the same way the working Review-CSV jump does so they match.
        fov_norm = review_row_keys(ctx.app,
            {"fov": desc.fov, "timepoint": desc.tp_token,
             "nucleus_id": desc.nucleus_id})[0]
        tp_norm = norm_timepoint(ctx, desc.tp_token)
        fov_cb = getattr(ctx.window, "_preview_fov_cb", None)
        if fov_cb is not None and fov_norm:
            fov_cb.setCurrentText(fov_norm)
        refresh_review_image(ctx.app)
        # The TP menu now holds the target FOV's timepoints; setting it fires
        # currentIndexChanged → refresh, rendering the correct frame.
        tp_cb = getattr(ctx.window, "_review_image_tp_cb", None)
        if tp_cb is not None and tp_norm:
            tp_cb.setCurrentText(tp_norm)
        # Centre + zoom on the highlighted cell so it's actually visible.
        zoom_review_image_to_selected_nucleus(ctx.app)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("cell_browser: navigate-to-segmentation failed")
        _set_status(ctx, f"Could not open cell in Segmentation: {exc}")


# ── helpers ──────────────────────────────────────────────────────────────────

def rgb_to_pixmap(rgb) -> QPixmap:
    """Convert an HxWx3 uint8 array to a QPixmap (copies the buffer)."""
    import numpy as np
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = a.shape[:2]
    img = QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img)


def _page_size(ctx) -> int:
    edit = getattr(ctx.runtime.cell_browser, "pagesize_edit", None)
    if edit is None:
        return ctx.runtime.cell_browser.state.page_size
    try:
        return max(1, int(float(edit.text().strip())))
    except (TypeError, ValueError):
        return ctx.runtime.cell_browser.state.page_size


def _clear_grid(ctx) -> None:
    grid = getattr(ctx.runtime.cell_browser, "grid", None)
    if grid is None:
        return
    while grid.count():
        item = grid.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


def _update_count(ctx) -> None:
    lbl = getattr(ctx.runtime.cell_browser, "count_label", None)
    if lbl is None:
        return
    st = ctx.runtime.cell_browser.state
    lbl.setText(f"Showing {st.loaded} / {len(st.cells)} cells")


def _set_status(ctx, msg: str) -> None:
    if True:
        try:
            ctx.set_status(msg)
        except Exception:
            swallowed()
