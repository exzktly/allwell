"""Inline Batch Export tab content builders (Qt port)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from well_viewer.features.batch_export import (
    BatchExportPanel,
    BarBatchExportPanel,
    ScatterBatchExportPanel,
)


def _refresh_mode_buttons(app, mode: str) -> None:
    """Sync the SegmentedControl's selected segment with the active builder."""
    state = getattr(app, "_batch_export_inline_state", None)
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


def _build_spread_row(app, parent: QWidget) -> QWidget:
    """Build the Error Band (SEM/SD) + per-FOV-spread toggle row.

    The buttons register into the same ``app._sem_btns`` / ``app._fov_btns``
    lists used by the matplotlib toolbars, so toggling here propagates
    everywhere and the visuals stay in sync. The line/bar batch panels read
    ``app._app_state.plot.use_sem`` / ``app._use_fov_spread_active()`` directly, so toggling
    these immediately changes what the next "Generate" produces.
    """
    row = QWidget(parent)
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 8)
    rl.setSpacing(6)

    eb_lbl = QLabel("Error Band:", row)
    eb_lbl.setObjectName("Muted")
    rl.addWidget(eb_lbl)

    initial_sem = bool(app._app_state.plot.use_sem)
    sem_btn = QPushButton("SEM" if initial_sem else "SD", row)
    sem_btn.setProperty("variant", "sem" if initial_sem else "sem_warn")
    sem_btn.setToolTip(
        "Switch the spread used by line/bar batch exports between\n"
        "SEM (Standard Error of the Mean) and SD (Standard Deviation).\n"
        "Mirrors the SEM/SD toggle in the plot toolbars."
    )
    sem_btn.clicked.connect(lambda _=False: app._toggle_sem())
    rl.addWidget(sem_btn)
    if not hasattr(app, "_sem_btns") or app._sem_btns is None:
        app._sem_btns = []
    app._sem_btns.append(sem_btn)
    if not getattr(app, "_sem_btn", None):
        app._sem_btn = sem_btn

    spread_lbl = QLabel("  Spread:", row)
    spread_lbl.setObjectName("Muted")
    rl.addWidget(spread_lbl)

    fov_btn = QPushButton("FOV", row)
    fov_btn.setProperty("variant", "toggle")
    fov_btn.clicked.connect(lambda _=False: app._toggle_fov_replicates())
    rl.addWidget(fov_btn)
    if not hasattr(app, "_fov_btns") or app._fov_btns is None:
        app._fov_btns = []
    app._fov_btns.append(fov_btn)
    if not getattr(app, "_fov_btn", None):
        app._fov_btn = fov_btn
    if hasattr(app, "_refresh_fov_btn_state"):
        app._refresh_fov_btn_state()

    rl.addStretch(1)
    return row


def _show_batch_mode(app, mode: str) -> None:
    state = getattr(app, "_batch_export_inline_state", None)
    if not state:
        return

    if mode not in ("line", "bar", "scatter_cells", "scatter_agg"):
        mode = "line"

    host = state["host"]
    host_layout = host.layout()
    panels = state["panels"]
    if mode not in panels:
        if mode == "line":
            panel = BatchExportPanel(app, host, use_sidebar_groups=False)
        elif mode == "bar":
            panel = BarBatchExportPanel(app, host, use_sidebar_groups=False)
        elif mode == "scatter_cells":
            panel = ScatterBatchExportPanel(
                app, host, scatter_mode="cells", use_sidebar_groups=False,
            )
        else:
            panel = ScatterBatchExportPanel(
                app, host, scatter_mode="aggregate", use_sidebar_groups=False,
            )
        host_layout.addWidget(panel)
        panels[mode] = panel
    for key, panel in panels.items():
        panel.setVisible(key == mode)

    # Resync the visible panel's groups from the live Sample Definitions
    # selections. The panel's __init__ snapshots app._rep_sets_loaded(),
    # which is empty when the user opens the line-plot sub-mode before
    # they've defined any selections; without this refresh the line panel
    # never picks up groups added later, while bar/scatter happened to work
    # because they were always opened after some selections existed.
    active_panel = panels.get(mode)
    if active_panel is not None and hasattr(active_panel, "_sync_from_app"):
        try:
            active_panel._sync_from_app()
        except Exception:
            pass

    state["mode"] = mode
    _refresh_mode_buttons(app, mode)


def _mode_button(parent: QWidget, text: str, command) -> QPushButton:
    b = QPushButton(text, parent)
    b.setProperty("variant", "toggle")
    b.clicked.connect(lambda _=False: command())
    return b


def build_batch_export_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(12, 12, 12, 12)

    title = QLabel("Batch Export", parent)
    title.setProperty("role", "section")
    layout.addWidget(title)

    subtitle = QLabel(
        "Configure and run inline line/bar batch export workflows from this tab.",
        parent,
    )
    subtitle.setObjectName("Muted")
    subtitle.setWordWrap(True)
    layout.addWidget(subtitle)

    layout.addWidget(_build_spread_row(ctx.app, parent))

    # v2: pick-one mode via SegmentedControl instead of a row of toggle
    # QPushButtons. Each segment carries the mode key in data; currentChanged
    # routes to the appropriate _open_*_batch_export entrypoint.
    from widgets.segmented_control import SegmentedControl as _SegmentedControl
    switch_row = _SegmentedControl(parent)
    switch_row.addSegment("Line", data="line")
    switch_row.addSegment("Bar", data="bar")
    switch_row.addSegment("Scatter (cells)", data="scatter_cells")
    switch_row.addSegment("Scatter (aggregate)", data="scatter_agg")

    _mode_dispatch = {
        "line": ctx.app._open_batch_export,
        "bar": ctx.app._open_bar_batch_export,
        "scatter_cells": ctx.app._open_scatter_cells_batch_export,
        "scatter_agg": ctx.app._open_scatter_agg_batch_export,
    }

    def _on_mode_changed(_idx: int) -> None:
        mode = switch_row.currentData() or "line"
        fn = _mode_dispatch.get(mode)
        if fn is not None:
            fn()
    switch_row.currentChanged.connect(_on_mode_changed)
    layout.addWidget(switch_row)

    host = QWidget(parent)
    QVBoxLayout(host)
    layout.addWidget(host, 1)

    ctx.app._batch_export_inline_state = {
        "host": host,
        "panels": {},
        "mode": "line",
        "mode_segmented": switch_row,
    }
    ctx.app._batch_export_set_mode = lambda mode="line": _show_batch_mode(ctx.app, mode)
    ctx.app._batch_export_set_mode("line")
