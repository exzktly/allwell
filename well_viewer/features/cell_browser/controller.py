"""Cell Browser orchestration: run the filter, page in thumbnails, navigate.

Reaches the app via the ``app`` argument (never imports ``WellViewerApp``).
Image loading is delegated to ``worker.CellImageLoader`` so the UI never blocks;
this module owns the page bookkeeping and turns worker results into grid tiles.
"""

from __future__ import annotations

import logging
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from well_viewer.core.safe import swallowed
from well_viewer.features.cell_browser.collect import Criterion, collect_cells
from well_viewer.features.cell_browser.state import MAX_TOTAL, THUMB_PX
from well_viewer.features.review_image.renderer import refresh_review_image

_logger = logging.getLogger("well_viewer")

GRID_COLS = 6


class _CellThumb(QLabel):
    """A clickable cell thumbnail — click highlights the cell in Segmentation."""

    def __init__(self, app, desc, parent=None) -> None:
        super().__init__(parent)
        self._app = app
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
        on_cell_clicked(self._app, self._desc)
        super().mousePressEvent(ev)


def make_cell_thumb(app, rgb, desc) -> QWidget:
    """Build one grid tile for a composed cell (placeholder when rgb is None)."""
    thumb = _CellThumb(app, desc)
    tp = f"t={desc.timepoint_h:g}h" if desc.timepoint_h == desc.timepoint_h else "t=?"
    thumb.setToolTip(
        f"{desc.well}  FOV {desc.fov}  {tp}\nnucleus {desc.nucleus_id}")
    if rgb is None:
        thumb.setText("—")
    else:
        pm = rgb_to_pixmap(rgb).scaled(
            THUMB_PX - 2, THUMB_PX - 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        thumb.setPixmap(pm)
    return thumb


# ── reading the filter/display widgets ───────────────────────────────────────

def read_criteria(app) -> List[Criterion]:
    out: List[Criterion] = []
    for combo, lo_edit, hi_edit in getattr(app, "_cell_browser_criteria_rows", []):
        col = combo.currentData()
        if not col:
            continue
        out.append(Criterion(col, _num(lo_edit.text()), _num(hi_edit.text())))
    return out


def read_timepoints(app) -> List[float]:
    sel = getattr(app, "_cell_browser_tp_selections", {}) or {}
    out: List[float] = []
    for tp_str, on in sel.items():
        if on:
            try:
                out.append(float(tp_str))
            except (TypeError, ValueError):
                swallowed()
    return out


def read_fovs(app) -> List[str]:
    sel = getattr(app, "_cell_browser_fov_selections", {}) or {}
    return [f for f, on in sel.items() if on]


def read_channels(app) -> List[str]:
    chips = getattr(app, "_cell_browser_channel_chips", {}) or {}
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

def apply_filters(app) -> None:
    """Collect the filtered cell set and load the first page of thumbnails."""
    st = app._cell_browser_state
    st.criteria = read_criteria(app)
    st.timepoints = read_timepoints(app)
    st.fovs = read_fovs(app)
    st.channels_on = read_channels(app)
    bcb = getattr(app, "_cell_browser_boundary_cb", None)
    st.show_boundaries = bool(bcb.isChecked()) if bcb is not None else True
    st.page_size = _page_size(app)

    cells = collect_cells(app, st.criteria, st.timepoints, st.fovs)
    if len(cells) > MAX_TOTAL:
        cells = cells[:MAX_TOTAL]
    st.cells = cells
    st.loaded = 0
    app._cell_browser_gen = getattr(app, "_cell_browser_gen", 0) + 1
    _clear_grid(app)
    _update_count(app)
    if not st.channels_on:
        _set_status(app, "Select at least one channel to display.")
        return
    load_next_page(app)


def load_next_page(app) -> None:
    st = app._cell_browser_state
    if st.remaining <= 0:
        _update_count(app)
        return
    page = st.cells[st.loaded:st.loaded + st.page_size]
    start = st.loaded
    loader = app._cell_browser_loader
    loader.load_page(page, start, list(st.channels_on),
                     st.show_boundaries, app._cell_browser_gen)
    # Optimistically advance; on_page_finished reconciles the count.
    st.loaded += len(page)
    _update_count(app)
    btn = getattr(app, "_cell_browser_load_more_btn", None)
    if btn is not None:
        btn.setEnabled(False)  # re-enabled when the page finishes


def on_cell_ready(app, payload: dict) -> None:
    """Queued-signal sink: add one composed thumbnail to the grid."""
    if payload.get("gen") != getattr(app, "_cell_browser_gen", 0):
        return  # superseded by a newer filter run
    idx = int(payload["index"])
    rgb = payload.get("rgb")
    desc = payload["desc"]
    tile = make_cell_thumb(app, rgb, desc)
    grid = getattr(app, "_cell_browser_grid", None)
    if grid is None:
        return
    grid.addWidget(tile, idx // GRID_COLS, idx % GRID_COLS)


def on_page_finished(app, payload: dict) -> None:
    if payload.get("gen") != getattr(app, "_cell_browser_gen", 0):
        return
    _update_count(app)
    btn = getattr(app, "_cell_browser_load_more_btn", None)
    if btn is not None:
        btn.setEnabled(app._cell_browser_state.remaining > 0)


def on_cell_clicked(app, desc) -> None:
    """Highlight the clicked cell in the Segmentation view (mirrors the
    Review-CSV jump-to-image navigation)."""
    try:
        if desc.nucleus_id:
            try:
                app._app_state.review_image.selected_nucleus = int(float(desc.nucleus_id))
            except (TypeError, ValueError):
                app._app_state.review_image.selected_nucleus = None
        app._app_state.preview.selected_well = desc.well
        if hasattr(app, "_notebook"):
            app._notebook.setCurrentByName("Segmentation")
        fov_cb = getattr(app, "_preview_fov_cb", None)
        if fov_cb is not None and desc.fov:
            fov_cb.setCurrentText(desc.fov)
        refresh_review_image(app)
        tp_cb = getattr(app, "_review_image_tp_cb", None)
        if tp_cb is not None and desc.tp_token:
            tp_cb.setCurrentText(desc.tp_token)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("cell_browser: navigate-to-segmentation failed")
        _set_status(app, f"Could not open cell in Segmentation: {exc}")


# ── helpers ──────────────────────────────────────────────────────────────────

def rgb_to_pixmap(rgb) -> QPixmap:
    """Convert an HxWx3 uint8 array to a QPixmap (copies the buffer)."""
    import numpy as np
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = a.shape[:2]
    img = QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img)


def _page_size(app) -> int:
    edit = getattr(app, "_cell_browser_pagesize_edit", None)
    if edit is None:
        return app._cell_browser_state.page_size
    try:
        return max(1, int(float(edit.text().strip())))
    except (TypeError, ValueError):
        return app._cell_browser_state.page_size


def _clear_grid(app) -> None:
    grid = getattr(app, "_cell_browser_grid", None)
    if grid is None:
        return
    while grid.count():
        item = grid.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


def _update_count(app) -> None:
    lbl = getattr(app, "_cell_browser_count_label", None)
    if lbl is None:
        return
    st = app._cell_browser_state
    lbl.setText(f"Showing {st.loaded} / {len(st.cells)} cells")


def _set_status(app, msg: str) -> None:
    fn = getattr(app, "_set_status", None)
    if callable(fn):
        try:
            fn(msg)
        except Exception:
            swallowed()
