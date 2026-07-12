"""Inline Batch Export tab content builders (Qt port)."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from well_viewer.features.batch_export import (
    BatchExportPanel,
    BarBatchExportPanel,
    ScatterBatchExportPanel,
)
# Shared plot toggles now live in core (A8 enabler E3), so this feature can drive
# the SEM/SD + per-FOV controls through ctx without a cross-feature import.

from PySide6.QtWidgets import QScrollArea


class _ResponsiveScroll(QScrollArea):
    """Vertical-scroll host that, on resize, pushes its viewport width into the
    Batch Export panels so they can flip between side-by-side and stacked. The
    viewport width is the true available width (the panel itself is pinned to its
    own wider side-by-side minimum with horizontal scroll off)."""

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        host = self.widget()
        if host is None:
            return
        avail = self.viewport().width()
        for panel in host.findChildren(BatchExportPanel):
            if panel.isVisible():
                panel.apply_split_orientation(avail)


def _refresh_mode_buttons(ctx, mode: str) -> None:
    """Sync the SegmentedControl's selected segment with the active builder."""
    state = ctx.runtime.batch_export.inline_state
    if not state:
        return
    sc = state.get("mode_segmented")
    if sc is None or sc.currentData() == mode:
        return
    blocked = sc.blockSignals(True)
    try:
        sc.setCurrentByData(mode)
    finally:
        sc.blockSignals(blocked)


def _show_batch_mode(ctx, mode: str) -> None:
    state = ctx.runtime.batch_export.inline_state
    if not state:
        return

    if mode not in ("line", "bar", "scatter_cells", "scatter_agg"):
        mode = "line"

    host = state["host"]
    host_layout = host.layout()
    panels = state["panels"]
    if mode not in panels:
        # Batch Export defines its OWN export groups (the left-panel editor).
        # It deliberately does NOT default to the sidebar replicate sets —
        # replicate groups are not the right unit for batch exports (reverts N10).
        if mode == "line":
            panel = BatchExportPanel(ctx, host, use_sidebar_groups=False)
        elif mode == "bar":
            panel = BarBatchExportPanel(ctx, host, use_sidebar_groups=False)
        elif mode == "scatter_cells":
            panel = ScatterBatchExportPanel(
                ctx, host, scatter_mode="cells", use_sidebar_groups=False,
            )
        else:
            panel = ScatterBatchExportPanel(
                ctx, host, scatter_mode="aggregate", use_sidebar_groups=False,
            )
        host_layout.addWidget(panel)
        panels[mode] = panel
    for key, panel in panels.items():
        panel.setVisible(key == mode)

    # Note: we deliberately do NOT auto-resync the panel's groups from the live
    # Sample Definitions on every tab show. Batch-export groups are export-
    # specific and start empty; re-seeding here used to wipe the user's export
    # groups with the replicate sets on each visit. Users pull in the replicate
    # groups explicitly via the panel's "Sync from app" button or Quick Setup.

    state["mode"] = mode
    _refresh_mode_buttons(ctx, mode)


def build_batch_export_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(12, 12, 12, 12)

    # v2: pick-one mode via SegmentedControl instead of a row of toggle
    # QPushButtons. Each segment carries the mode key in data; currentChanged
    # routes to the appropriate _open_*_batch_export entrypoint. The Error-band /
    # per-FOV spread controls now live in each line/bar panel's OUTPUT SETTINGS.
    from widgets.segmented_control import SegmentedControl as _SegmentedControl
    switch_row = _SegmentedControl(parent)
    switch_row.addSegment("Line", data="line")
    switch_row.addSegment("Bar", data="bar")
    switch_row.addSegment("Scatter (cells)", data="scatter_cells")
    switch_row.addSegment("Scatter (aggregate)", data="scatter_agg")

    from well_viewer.core.plot_orchestrator import (
        open_batch_export_shim,
        open_bar_batch_export_shim,
        open_scatter_cells_batch_export_shim,
        open_scatter_agg_batch_export_shim,
    )

    _mode_dispatch = {
        "line": open_batch_export_shim,
        "bar": open_bar_batch_export_shim,
        "scatter_cells": open_scatter_cells_batch_export_shim,
        "scatter_agg": open_scatter_agg_batch_export_shim,
    }

    def _on_mode_changed(_idx: int) -> None:
        mode = switch_row.currentData() or "line"
        fn = _mode_dispatch.get(mode)
        if fn is not None:
            fn(ctx)
    switch_row.currentChanged.connect(_on_mode_changed)
    layout.addWidget(switch_row)

    # The Error-band (SEM/SD) + per-FOV-points toggles used to sit in a separate
    # row here; they now live inside each line/bar panel's OUTPUT SETTINGS (see
    # BatchExportPanel._build_spread_settings_rows), with the rest of the config.

    # Host the mode panels inside a vertical-scroll area: when the window is
    # narrow the panel stacks its plate-map / OUTPUT-SETTINGS vertically (see
    # BatchExportPanel.apply_split_orientation), which grows taller — vertical
    # scroll keeps it usable on short windows, and horizontal scroll stays off so
    # nothing ever scrolls sideways (the stack removes horizontal overflow). The
    # scroll pushes its viewport width into the panels on resize, since with
    # h-scroll off a panel is pinned to its own (wide) min and can't measure the
    # narrow window itself.
    from PySide6.QtWidgets import QFrame
    from PySide6.QtCore import Qt
    host = QWidget()
    QVBoxLayout(host)
    scroll = _ResponsiveScroll(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setWidget(host)
    layout.addWidget(scroll, 1)

    ctx.runtime.batch_export.inline_state = {
        "host": host,
        "panels": {},
        "mode": "line",
        "mode_segmented": switch_row,
    }
    ctx.runtime.batch_export.set_mode = lambda mode="line": _show_batch_mode(ctx, mode)
    ctx.runtime.batch_export.set_mode("line")
