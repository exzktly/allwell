"""Cell Browser centre tab: display controls + a scrollable grid of cell images.

The filter (criteria + timepoint/FOV pickers) lives in the left sidebar
(``criteria_view``); this builds the centre — a controls row (channel toggles,
boundary toggle, page size, Load-next, count) above a ``QScrollArea`` grid that
fills incrementally as the worker composes thumbnails.
"""

from __future__ import annotations

from functools import partial

import theme
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from widgets.control_band import ControlBand

from well_viewer.features.image_browser.cell_browser import controller as ctl
from well_viewer.features.image_browser.cell_browser.criteria_view import (  # re-export
    build_cell_browser_sidebar,
    cell_browser_sidebar_refresh,
)
from well_viewer.features.image_browser.cell_browser.collect import cell_browser_channels
from well_viewer.features.image_browser.cell_browser.state import CellBrowserState
from well_viewer.features.image_browser.cell_browser.worker import (
    CellImageLoader,
    resolve_channel_tint,
)

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_SWATCH_H = 18
_HI_E_W = 60
_LO_E_W = 60
_PAGESIZE_EDIT_W = 48
_RESET_C_W = 22

__all__ = [
    "build_cell_browser_tab",
    "cell_browser_refresh",
    "build_cell_browser_sidebar",
    "cell_browser_sidebar_refresh",
]


def build_cell_browser_tab(ctx, parent: QWidget) -> None:
    # A8 burn-down: Qt widget handles go through the WidgetRegistry
    # (``ctx.widgets``), which stamps ``ctx._cell_browser_*`` so legacy
    # readers resolve. State dicts + the loader/refreshers stay on ctx.
    reg = ctx.widgets
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    ctx.runtime.cell_browser.state = CellBrowserState()

    # ── controls row ────────────────────────────────────────────────────────
    ctrl = ControlBand(parent)

    chan_host = QWidget()
    QHBoxLayout(chan_host).setContentsMargins(0, 0, 0, 0)
    ctrl.add_scope("Channels", chan_host)
    reg.register("_cell_browser_channel_host", chan_host)
    ctx.runtime.cell_browser.channel_chips = {}

    # Per-channel LUT editor (min/max) in a popover. Edits the SHARED
    # per-channel display LUT (AppState.image_view, S4), so brightness tuned
    # here also applies in Review Image / Image Table; the rows are rebuilt to
    # track the dataset's channels in ``cell_browser_refresh``.
    from well_viewer.core.ui_helpers import make_popover_button
    lut_btn, lut_panel, lut_lay = make_popover_button("LUT ▾", ctrl)
    lut_btn.setToolTip(
        "Per-channel display brightness (min/max). Blank / Auto = auto-scale each "
        "cell. Shared with the other image viewers.")
    ctrl.add_action(lut_btn)
    reg.register("_cell_browser_lut_btn", lut_btn)
    reg.register("_cell_browser_lut_panel", lut_panel)
    reg.register("_cell_browser_lut_layout", lut_lay)
    ctx.runtime.cell_browser.lut_rows = {}

    # Secondary display toggles (Boundaries / Spot counts / Source stage) collapse
    # into a "Display ▾" popover so the row stays readable at narrow (Split View)
    # widths — Channels / LUT / Generate are the controls that matter inline.
    disp_btn, disp_panel, disp_lay = make_popover_button("Display ▾", ctrl)
    disp_btn.setToolTip("Cell display options: boundaries, spot counts, image source")
    ctrl.add_action(disp_btn)

    boundary_cb = QCheckBox("Boundaries", disp_panel)
    boundary_cb.setChecked(True)
    disp_lay.addWidget(boundary_cb)
    reg.register("_cell_browser_boundary_cb", boundary_cb)

    # N9: caption each crop with the active channel's smFISH spot count. Staged
    # like the other display controls — applied on Generate.
    label_spot_cb = QCheckBox("Spot counts", disp_panel)
    label_spot_cb.setChecked(False)
    label_spot_cb.setToolTip(
        "Label each cell with the active channel's smFISH spot count "
        "(its <channel>_smfish_count). Sort by that metric to rank cells by count.")
    disp_lay.addWidget(label_spot_cb)
    reg.register("_cell_browser_label_spot_cb", label_spot_cb)

    # Source: which stage of the fluorescence image to crop from. Raw (default)
    # or Corrected (top-hat). Staged like the rest — applied on Generate.
    from widgets.segmented_control import SegmentedControl as _SegmentedControl
    _src_row = QWidget(disp_panel)
    _src_lay = QHBoxLayout(_src_row)
    _src_lay.setContentsMargins(0, 0, 0, 0)
    _src_lay.setSpacing(6)
    _src_lay.addWidget(QLabel("Source:", _src_row))
    source_seg = _SegmentedControl(_src_row)
    for _lbl, _data in (("Raw", "raw"), ("Corrected", "corrected")):
        source_seg.addSegment(_lbl, data=_data)
    source_seg.setCurrentByData("raw")
    source_seg.setToolTip(
        "Image stage cropped for each cell:\n"
        "• Raw — unprocessed fluorescence.\n"
        "• Corrected — top-hat / background-subtracted image.")
    _src_lay.addWidget(source_seg)
    disp_lay.addWidget(_src_row)
    reg.register("_cell_browser_source_seg", source_seg)

    pagesize_edit = QLineEdit("50")
    pagesize_edit.setFixedWidth(_PAGESIZE_EDIT_W)
    ctrl.add_scope("Per page", pagesize_edit)
    reg.register("_cell_browser_pagesize_edit", pagesize_edit)

    count_lbl = QLabel("Showing 0 / 0 cells")
    count_lbl.setObjectName("Muted")
    ctrl.add_scope("", count_lbl)
    reg.register("_cell_browser_count_label", count_lbl)

    load_more = QPushButton("Load next", ctrl)
    load_more.setProperty("variant", "secondary")
    load_more.clicked.connect(lambda _=False: ctl.load_next_page(ctx))
    load_more.setEnabled(False)
    ctrl.add_action(load_more)
    reg.register("_cell_browser_load_more_btn", load_more)

    # Generate: the display controls (channels / LUT / color / source /
    # boundaries) only *stage* their state; pressing Generate re-composites the
    # gallery with everything staged (same gate as the sidebar "Apply filters").
    generate_btn = QPushButton("Generate", ctrl)
    generate_btn.setObjectName("Primary")
    generate_btn.setToolTip(
        "Re-render the gallery with the current channels / LUT / colors / "
        "source. Display settings are staged until you press this.")
    generate_btn.clicked.connect(lambda _=False: ctl.apply_filters(ctx))
    ctrl.add_action(generate_btn)
    reg.register("_cell_browser_generate_btn", generate_btn)
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
    from well_viewer.core.ui_helpers import EmptyReason, wrap_with_empty_state

    def _nothing_generated(c):
        # Not selection-gated — this gallery follows the plotting layer's active
        # wells and its own cell filters, not the plate. What it *is* gated on is
        # having been generated: before that the grid is empty, and the body was
        # a blank panel whose only explanation was a status-bar line.
        st = c.runtime.cell_browser.state
        if getattr(st, "cells", None):
            return None
        return EmptyReason(
            "No cells shown yet.",
            "Press Generate to build the gallery from the current filters.",
        )

    layout.addWidget(wrap_with_empty_state(
        ctx.window, scroll, icon="boxes", reason=_nothing_generated,
    ), 1)
    reg.register("_cell_browser_grid", grid)
    reg.register("_cell_browser_grid_host", grid_host)

    # ── worker (connect once) ────────────────────────────────────────────────
    loader = CellImageLoader(ctx)
    loader.cell_ready.connect(partial(_dispatch_cell_ready, ctx))
    loader.page_finished.connect(partial(_dispatch_page_finished, ctx))
    ctx.runtime.cell_browser.loader = loader

    # Expose the on-show refreshers so ``tab_controller.on_tab_change`` can
    # repopulate channels/criteria/timepoints on every visit without a
    # function-local feature import (keeps the deferred-import budget flat).
    ctx.runtime.cell_browser.refreshers = (
        cell_browser_refresh,
        cell_browser_sidebar_refresh,
    )

    cell_browser_refresh(ctx)


def _dispatch_cell_ready(ctx, payload) -> None:
    ctl.on_cell_ready(ctx, payload)


def _dispatch_page_finished(ctx, payload) -> None:
    ctl.on_page_finished(ctx, payload)


def cell_browser_refresh(ctx) -> None:
    """Repopulate the channel toggles from the current dataset (called on tab
    show / dataset load). Preserves existing checked state where channels match."""
    host = getattr(ctx.runtime.cell_browser, "channel_host", None)
    if host is None:
        return
    channels = cell_browser_channels(ctx)
    if not channels:
        # A transient empty resolve (channel state not populated yet, rows not
        # cached on this pass) must NOT wipe a channel row that already built —
        # clearing then adding nothing is exactly how the bar "vanished after it
        # showed once". Leave any existing chips intact and try again next refresh.
        return
    prev = {tok: cb.isChecked() for tok, cb in
            (getattr(ctx.runtime.cell_browser, "channel_chips", {}) or {}).items()}
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
        # Staged: the chip state is read on Generate (read_channels), so toggling
        # a channel doesn't re-composite the gallery on its own.
        lay.addWidget(cb)
        chips[tok] = cb
    ctx.runtime.cell_browser.channel_chips = chips
    cell_browser_refresh_lut_rows(ctx)


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


def _apply_cell_browser_lut(ctx, channel: str, lo_text: str, hi_text: str) -> None:
    """Write a channel's bounds into the shared LUT (or clear → auto).

    Staged only: the gallery re-composites on the next Generate, not on edit."""
    iv = getattr(ctx.state, "image_view", None)
    if iv is None:
        return
    lo, hi = _cb_num(lo_text), _cb_num(hi_text)
    if lo is not None and hi is not None and hi > lo:
        iv.set_lut(channel, lo, hi)
    else:
        iv.lut_by_channel.pop(str(channel).strip().lower(), None)  # back to auto


def _cb_effective_tint(iv, channel: str, index: int):
    """The ``(r, g, b)`` 0–255 colour shown for a channel: override, else the
    default per-index palette colour."""
    override = iv.get_tint(channel) if iv is not None else None
    if override is not None:
        return tuple(int(c) for c in override)
    r, g, b = resolve_channel_tint(channel, index, None)  # 0..1 default
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _paint_cb_swatch(btn, rgb) -> None:
    r, g, b = rgb
    btn.setStyleSheet(
        f"QPushButton {{ background-color: rgb({r},{g},{b}); "
        f"border: 1px solid {theme.Colors.border_strong}; border-radius: 4px; "
        f"min-width: 28px; min-height: 18px; padding: 0; }}")


def _cb_pick_color(ctx, channel: str, index: int, swatch_btn) -> None:
    """Open a colour popover for *channel*; picking stages the per-channel tint
    (applied on the next Generate)."""
    from PySide6.QtGui import QColor
    from widgets.color_swatch_row import ColorSwatchRow
    from widgets.popover import Popover

    iv = getattr(ctx.state, "image_view", None)
    if iv is None:
        return
    r, g, b = _cb_effective_tint(iv, channel, index)
    pop = Popover(swatch_btn)
    row = ColorSwatchRow(allow_custom=True)
    row.setCurrentColor(QColor(r, g, b))

    def _apply(c, _ch=channel, _btn=swatch_btn) -> None:
        if not c.isValid():
            return
        iv.set_tint(_ch, (c.red(), c.green(), c.blue()))
        _paint_cb_swatch(_btn, (c.red(), c.green(), c.blue()))

    row.colorPicked.connect(_apply)
    pop.setContentWidget(row)
    pop.popup(swatch_btn, side="bottom", align="start")


def cell_browser_refresh_lut_rows(ctx) -> None:
    """(Re)build the per-channel LUT editor rows from the dataset's channels.

    Each row edits the SHARED per-channel LUT; fields are seeded from it (blank
    when unset = auto). Called alongside the channel-chip refresh."""
    lay = getattr(ctx.runtime.cell_browser, "lut_layout", None)
    panel = getattr(ctx.runtime.cell_browser, "lut_panel", None)
    if lay is None or panel is None:
        return
    channels = cell_browser_channels(ctx)
    if not channels:
        return
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    iv = getattr(ctx.state, "image_view", None)
    rows = {}
    for i, tok in enumerate(channels):
        row = QWidget(panel)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        name = QLabel(tok.upper(), row)
        name.setFixedWidth(theme.Metrics.short_field_w)
        rl.addWidget(name)
        lut = iv.get_lut(tok) if iv is not None else None
        rl.addWidget(QLabel("min", row))
        lo_e = QLineEdit(_cb_fmt(None if lut is None else lut[0]), row)
        lo_e.setFixedWidth(_LO_E_W)
        lo_e.setPlaceholderText("auto")
        rl.addWidget(lo_e)
        rl.addWidget(QLabel("max", row))
        hi_e = QLineEdit(_cb_fmt(None if lut is None else lut[1]), row)
        hi_e.setFixedWidth(_HI_E_W)
        hi_e.setPlaceholderText("auto")
        rl.addWidget(hi_e)
        auto_btn = QPushButton("Auto", row)
        auto_btn.setProperty("variant", "secondary")
        rl.addWidget(auto_btn)

        # Per-channel display colour: a swatch (opens a colour popover) + a reset
        # back to the default palette colour. Staged → applied on Generate.
        rl.addWidget(QLabel("color", row))
        swatch = QPushButton("", row)
        swatch.setFixedSize(theme.Metrics.swatch_w, _SWATCH_H)
        swatch.setToolTip("Display colour for this channel. Applied on Generate.")
        _paint_cb_swatch(swatch, _cb_effective_tint(iv, tok, i))
        rl.addWidget(swatch)
        reset_c = QPushButton("✕", row)
        reset_c.setFixedWidth(_RESET_C_W)
        reset_c.setObjectName("Ghost")
        reset_c.setToolTip("Reset this channel's colour to the default.")
        rl.addWidget(reset_c)

        def _commit(_=None, _t=tok, _lo=lo_e, _hi=hi_e):
            _apply_cell_browser_lut(ctx, _t, _lo.text(), _hi.text())

        def _auto(_=None, _t=tok, _lo=lo_e, _hi=hi_e):
            _lo.clear()
            _hi.clear()
            _apply_cell_browser_lut(ctx, _t, "", "")

        def _pick(_=None, _t=tok, _i=i, _sw=swatch):
            _cb_pick_color(ctx, _t, _i, _sw)

        def _reset_color(_=None, _t=tok, _i=i, _sw=swatch):
            if iv is not None:
                iv.set_tint(_t, None)
            _paint_cb_swatch(_sw, _cb_effective_tint(iv, _t, _i))

        lo_e.editingFinished.connect(_commit)
        hi_e.editingFinished.connect(_commit)
        auto_btn.clicked.connect(_auto)
        swatch.clicked.connect(_pick)
        reset_c.clicked.connect(_reset_color)
        lay.addWidget(row)
        rows[tok] = (lo_e, hi_e)
    ctx.runtime.cell_browser.lut_rows = rows
