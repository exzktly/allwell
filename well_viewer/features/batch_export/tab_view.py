"""Inline Batch Export tab content builders (Qt port)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from well_viewer.features.batch_export import (
    BatchExportPanel,
    BarBatchExportPanel,
    ScatterBatchExportPanel,
)
# Shared plot toggles now live in core (A8 enabler E3), so this feature can drive
# the SEM/SD + per-FOV controls through ctx without a cross-feature import.
from well_viewer.core.plot_toggles import (
    toggle_sem,
    toggle_fov_replicates,
    refresh_fov_btn_state,
)


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


def _build_spread_row(ctx, parent: QWidget) -> QWidget:
    """Build the Error Band (SEM/SD) + per-FOV-spread toggle row.

    The buttons register into the same ``ctx.runtime.sem.btns`` /
    ``ctx.runtime.fov.btns`` banks used by the matplotlib toolbars, so toggling
    here propagates everywhere and the visuals stay in sync. The line/bar batch
    panels read ``ctx.state.plot.use_sem`` / the per-FOV flag directly, so
    toggling these immediately changes what the next "Generate" produces.
    """
    row = QWidget(parent)
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 8)
    rl.setSpacing(6)

    eb_lbl = QLabel("Error Band:", row)
    eb_lbl.setObjectName("Muted")
    rl.addWidget(eb_lbl)

    initial_sem = bool(ctx.state.plot.use_sem)
    sem_btn = QPushButton("SEM" if initial_sem else "SD", row)
    sem_btn.setProperty("variant", "sem" if initial_sem else "sem_warn")
    sem_btn.setToolTip(
        "Switch the spread used by line/bar batch exports between\n"
        "SEM (Standard Error of the Mean) and SD (Standard Deviation).\n"
        "Mirrors the SEM/SD toggle in the plot toolbars."
    )
    sem_btn.clicked.connect(lambda _=False: toggle_sem(ctx))
    rl.addWidget(sem_btn)
    if ctx.runtime.sem.btns is None:
        ctx.runtime.sem.btns = []
    ctx.runtime.sem.btns.append(sem_btn)
    if not ctx.runtime.sem.btn:
        ctx.runtime.sem.btn = sem_btn

    spread_lbl = QLabel("  Per-FOV points:", row)
    spread_lbl.setObjectName("Muted")
    rl.addWidget(spread_lbl)

    fov_btn = QPushButton("FOV", row)
    fov_btn.setProperty("variant", "toggle")
    fov_btn.clicked.connect(lambda _=False: toggle_fov_replicates(ctx))
    rl.addWidget(fov_btn)
    if ctx.runtime.fov.btns is None:
        ctx.runtime.fov.btns = []
    ctx.runtime.fov.btns.append(fov_btn)
    if not ctx.runtime.fov.btn:
        ctx.runtime.fov.btn = fov_btn
    refresh_fov_btn_state(ctx)

    rl.addStretch(1)
    return row


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

    # The Error-Band / per-FOV row only affects line & bar exports — hide it on
    # the scatter modes so it doesn't read as an applicable (but inert) control.
    band_row = state.get("band_row")
    if band_row is not None:
        band_row.setVisible(mode in ("line", "bar"))

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
    # routes to the appropriate _open_*_batch_export entrypoint. The chart-type
    # selector leads; the (line/bar-only) Error-Band row sits beneath it.
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

    # Error-Band / per-FOV row — beneath the chart-type selector, and only
    # shown for the line/bar modes it actually affects (toggled in
    # ``_show_batch_mode``).
    band_row = _build_spread_row(ctx, parent)
    layout.addWidget(band_row)

    host = QWidget(parent)
    QVBoxLayout(host)
    layout.addWidget(host, 1)

    ctx.runtime.batch_export.inline_state = {
        "host": host,
        "panels": {},
        "mode": "line",
        "mode_segmented": switch_row,
        "band_row": band_row,
    }
    ctx.runtime.batch_export.set_mode = lambda mode="line": _show_batch_mode(ctx, mode)
    ctx.runtime.batch_export.set_mode("line")
