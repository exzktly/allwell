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
from well_viewer.features.cell_browser.collect import cell_browser_channels
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

    # Per-channel LUT editor (min/max) in a popover. Edits the SHARED
    # per-channel display LUT (AppState.image_view, S4), so brightness tuned
    # here also applies in Review Image / Image Table; the rows are rebuilt to
    # track the dataset's channels in ``cell_browser_refresh``.
    from well_viewer.core.ui_helpers import make_popover_button
    lut_btn, lut_panel, lut_lay = make_popover_button("LUT ▾", ctrl)
    lut_btn.setToolTip(
        "Per-channel display brightness (min/max). Blank / Auto = auto-scale each "
        "cell. Shared with the other image viewers.")
    cl.addWidget(lut_btn)
    ctx.app._cell_browser_lut_btn = lut_btn
    ctx.app._cell_browser_lut_panel = lut_panel
    ctx.app._cell_browser_lut_layout = lut_lay
    ctx.app._cell_browser_lut_rows = {}

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
    channels = cell_browser_channels(app)
    if not channels:
        # A transient empty resolve (channel state not populated yet, rows not
        # cached on this pass) must NOT wipe a channel row that already built —
        # clearing then adding nothing is exactly how the bar "vanished after it
        # showed once". Leave any existing chips intact and try again next refresh.
        return
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
    cell_browser_refresh_lut_rows(app)


def _cb_num(text):
    """Parse a LUT field → float, or None for blank / 'auto' / unparseable."""
    text = (text or "").strip().lower()
    if not text or text == "auto":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _cb_fmt(v) -> str:
    """Format a LUT bound for a field; blank for None."""
    if v is None:
        return ""
    return f"{v:g}"


def _apply_cell_browser_lut(app, channel: str, lo_text: str, hi_text: str) -> None:
    """Write a channel's bounds into the shared LUT (or clear → auto) and
    re-composite the gallery so the change shows immediately."""
    iv = getattr(app._app_state, "image_view", None)
    if iv is None:
        return
    lo, hi = _cb_num(lo_text), _cb_num(hi_text)
    if lo is not None and hi is not None and hi > lo:
        iv.set_lut(channel, lo, hi)
    else:
        iv.lut_by_channel.pop(str(channel).strip().lower(), None)  # back to auto
    ctl.apply_filters(app)


def cell_browser_refresh_lut_rows(app) -> None:
    """(Re)build the per-channel LUT editor rows from the dataset's channels.

    Each row edits the SHARED per-channel LUT; fields are seeded from it (blank
    when unset = auto). Called alongside the channel-chip refresh."""
    lay = getattr(app, "_cell_browser_lut_layout", None)
    panel = getattr(app, "_cell_browser_lut_panel", None)
    if lay is None or panel is None:
        return
    channels = cell_browser_channels(app)
    if not channels:
        return
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    iv = getattr(app._app_state, "image_view", None)
    rows = {}
    for tok in channels:
        row = QWidget(panel)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        name = QLabel(tok.upper(), row)
        name.setFixedWidth(64)
        rl.addWidget(name)
        lut = iv.get_lut(tok) if iv is not None else None
        rl.addWidget(QLabel("min", row))
        lo_e = QLineEdit(_cb_fmt(None if lut is None else lut[0]), row)
        lo_e.setFixedWidth(60)
        lo_e.setPlaceholderText("auto")
        rl.addWidget(lo_e)
        rl.addWidget(QLabel("max", row))
        hi_e = QLineEdit(_cb_fmt(None if lut is None else lut[1]), row)
        hi_e.setFixedWidth(60)
        hi_e.setPlaceholderText("auto")
        rl.addWidget(hi_e)
        auto_btn = QPushButton("Auto", row)
        auto_btn.setProperty("variant", "secondary")
        rl.addWidget(auto_btn)

        def _commit(_=None, _t=tok, _lo=lo_e, _hi=hi_e):
            _apply_cell_browser_lut(app, _t, _lo.text(), _hi.text())

        def _auto(_=None, _t=tok, _lo=lo_e, _hi=hi_e):
            _lo.clear()
            _hi.clear()
            _apply_cell_browser_lut(app, _t, "", "")

        lo_e.editingFinished.connect(_commit)
        hi_e.editingFinished.connect(_commit)
        auto_btn.clicked.connect(_auto)
        lay.addWidget(row)
        rows[tok] = (lo_e, hi_e)
    app._cell_browser_lut_rows = rows
