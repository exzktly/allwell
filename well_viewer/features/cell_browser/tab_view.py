"""Cell Browser centre tab: display controls + a scrollable grid of cell images.

The filter (criteria + timepoint/FOV pickers) lives in the left sidebar
(``criteria_view``); this builds the centre — a controls row (channel toggles,
boundary toggle, page size, Load-next, count) above a ``QScrollArea`` grid that
fills incrementally as the worker composes thumbnails.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from well_viewer.features.cell_browser import controller as ctl
from well_viewer.features.cell_browser.criteria_view import (  # re-export
    build_cell_browser_sidebar,
    cell_browser_sidebar_refresh,
)
from well_viewer.features.cell_browser.state import CellBrowserState
from well_viewer.features.cell_browser.worker import CellImageLoader

__all__ = [
    "build_cell_browser_tab",
    "cell_browser_refresh",
    "build_cell_browser_sidebar",
    "cell_browser_sidebar_refresh",
]


def build_cell_browser_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    ctx.app._cell_browser_state = CellBrowserState()

    # ── controls row ────────────────────────────────────────────────────────
    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)
    cl.setSpacing(6)

    cl.addWidget(QLabel("Channels:", ctrl))
    chan_host = QWidget(ctrl)
    QHBoxLayout(chan_host).setContentsMargins(0, 0, 0, 0)
    cl.addWidget(chan_host)
    ctx.app._cell_browser_channel_host = chan_host
    ctx.app._cell_browser_channel_chips = {}

    boundary_cb = QCheckBox("Boundaries", ctrl)
    boundary_cb.setChecked(True)
    cl.addWidget(boundary_cb)
    ctx.app._cell_browser_boundary_cb = boundary_cb

    cl.addWidget(QLabel("Per page:", ctrl))
    pagesize_edit = QLineEdit("50", ctrl)
    pagesize_edit.setFixedWidth(48)
    cl.addWidget(pagesize_edit)
    ctx.app._cell_browser_pagesize_edit = pagesize_edit

    load_more = QPushButton("Load next", ctrl)
    load_more.setProperty("variant", "secondary")
    load_more.clicked.connect(lambda _=False: ctl.load_next_page(ctx.app))
    load_more.setEnabled(False)
    cl.addWidget(load_more)
    ctx.app._cell_browser_load_more_btn = load_more

    count_lbl = QLabel("Showing 0 / 0 cells", ctrl)
    count_lbl.setObjectName("Muted")
    cl.addWidget(count_lbl)
    ctx.app._cell_browser_count_label = count_lbl
    cl.addStretch(1)
    layout.addWidget(ctrl)

    # ── scrollable grid ──────────────────────────────────────────────────────
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    grid_host = QWidget()
    grid = QGridLayout(grid_host)
    grid.setContentsMargins(10, 10, 10, 10)
    grid.setSpacing(6)
    grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    scroll.setWidget(grid_host)
    layout.addWidget(scroll, 1)
    ctx.app._cell_browser_grid = grid
    ctx.app._cell_browser_grid_host = grid_host

    # ── worker (connect once) ────────────────────────────────────────────────
    loader = CellImageLoader(ctx.app)
    loader.cell_ready.connect(partial(_dispatch_cell_ready, ctx.app))
    loader.page_finished.connect(partial(_dispatch_page_finished, ctx.app))
    ctx.app._cell_browser_loader = loader

    # Expose the on-show refreshers so ``tab_controller.on_tab_change`` can
    # repopulate channels/criteria/timepoints on every visit without a
    # function-local feature import (keeps the deferred-import budget flat).
    ctx.app._cell_browser_refreshers = (
        cell_browser_refresh,
        cell_browser_sidebar_refresh,
    )

    cell_browser_refresh(ctx.app)


def _dispatch_cell_ready(app, payload) -> None:
    ctl.on_cell_ready(app, payload)


def _dispatch_page_finished(app, payload) -> None:
    ctl.on_page_finished(app, payload)


def cell_browser_refresh(app) -> None:
    """Repopulate the channel toggles from the current dataset (called on tab
    show / dataset load). Preserves existing checked state where channels match."""
    host = getattr(app, "_cell_browser_channel_host", None)
    if host is None:
        return
    channels = list(app._app_state.channel.fluor_channels or [])
    prev = {tok: cb.isChecked() for tok, cb in
            (getattr(app, "_cell_browser_channel_chips", {}) or {}).items()}
    lay = host.layout()
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    chips = {}
    for tok in channels:
        cb = QCheckBox(tok.upper(), host)
        cb.setChecked(prev.get(tok, True))
        # Toggling a channel re-composes the gallery so the choice takes effect
        # immediately (connected after setChecked so the programmatic state
        # above doesn't fire a reload).
        cb.toggled.connect(lambda _on, _app=app: ctl.apply_filters(_app))
        lay.addWidget(cb)
        chips[tok] = cb
    app._cell_browser_channel_chips = chips
