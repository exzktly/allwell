"""Inline Batch Export tab content builders (Qt port)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from widgets.page_body import build_page_body
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

    def resizeEvent(self, event) -> None:  # Qt override
        super().resizeEvent(event)
        host = self.widget()
        if host is None:
            return
        avail = self.viewport().width()
        for panel in host.findChildren(BatchExportPanel):
            if panel.isVisible():
                panel.apply_split_orientation(avail)


#: The page's four modes, in bar order, paired with the key each one dispatches
#: on. The bar carries no user data of its own — a ``QTabBar`` indexes by
#: position — so the order here IS the mapping.
_MODES = (
    ("Line", "line"),
    ("Bar", "bar"),
    ("Scatter (cells)", "scatter_cells"),
    ("Scatter (aggregate)", "scatter_agg"),
)


def _refresh_mode_buttons(ctx, mode: str) -> None:
    """Sync the mode bar's selected tab with the active builder."""
    state = ctx.runtime.batch_export.inline_state
    if not state:
        return
    bar = state.get("mode_bar")
    if bar is None:
        return
    idx = next((i for i, (_lbl, key) in enumerate(_MODES) if key == mode), -1)
    if idx < 0 or bar.currentIndex() == idx:
        return
    blocked = bar.blockSignals(True)
    try:
        bar.setCurrentIndex(idx)
    finally:
        bar.blockSignals(blocked)


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

    # Every mode shares one set of export groups, but each panel keeps its own
    # in-memory copy and is built once and cached — so a panel revealed after
    # another mode edited the groups would still be showing the list it
    # hydrated with. Re-read the shared slot on the way in.
    #
    # This is NOT the auto-resync the note below rules out: that one re-seeds
    # from the live sample-definition replicate sets, which used to wipe the
    # user's export groups on every visit. This re-reads the panel's own
    # export-groups document, which is how an edit made one mode ago survives.
    panels[mode]._reload_persisted_groups()

    # Note: we deliberately do NOT auto-resync the panel's groups from the live
    # the definitions on every tab show. Batch-export groups are export-
    # specific and start empty; re-seeding here used to wipe the user's export
    # groups with the replicate sets on each visit. Users pull in the replicate
    # groups explicitly via the panel's "Sync from app" button or Quick Setup.

    state["mode"] = mode
    _refresh_mode_buttons(ctx, mode)


def build_batch_export_tab(ctx, parent: QWidget) -> None:
    band, body = build_page_body(parent, content_margins=(12, 12, 12))
    layout = body.layout()

    # The four modes are a page selector, so the bar carrying them lives in the
    # shell's mode band beside the Review / Analyze / Datasets segments, exactly
    # like Plotting's and Image Browser's sub-tab bars — not as a fifth strip of
    # chrome inside the page. ``PlottingSubTabs`` is the object name theme.qss
    # styles every nested page bar with, so the three cannot drift apart. The
    # Error-band / per-FOV spread controls live in each line/bar panel's OUTPUT
    # SETTINGS.
    from PySide6.QtWidgets import QTabBar
    mode_bar = QTabBar()
    mode_bar.setObjectName("PlottingSubTabs")
    mode_bar.setExpanding(False)
    mode_bar.setElideMode(Qt.ElideNone)
    mode_bar.setUsesScrollButtons(True)
    mode_bar.setDrawBase(False)
    for label, _key in _MODES:
        mode_bar.addTab(label)
    from well_viewer.core.app_shell_view import register_page_tabs
    register_page_tabs(ctx.window, "Batch Export", mode_bar)

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

    def _on_mode_changed(idx: int) -> None:
        mode = _MODES[idx][1] if 0 <= idx < len(_MODES) else "line"
        fn = _mode_dispatch.get(mode)
        if fn is not None:
            fn(ctx)
    mode_bar.currentChanged.connect(_on_mode_changed)

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
    host = QWidget()
    QVBoxLayout(host)
    scroll = _ResponsiveScroll(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setWidget(host)
    # Dataset-gated only, deliberately. The obvious next step would be to gate
    # this on "no export groups" the way Statistics is gated on "no comparison
    # groups", but the widget wrapped here is the *whole* panel — plate map,
    # group list, OUTPUT SETTINGS and the Run Batch Export button. Swapping all
    # of that for a placeholder would hide the output configuration precisely
    # while the user is setting it up. Its "No export groups." line already sits
    # inside the group list, which is where the groups themselves would appear.
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, scroll, icon="download",
    ), 1)

    ctx.runtime.batch_export.inline_state = {
        "host": host,
        "panels": {},
        "mode": "line",
        "mode_bar": mode_bar,
        "band": band,
    }
    ctx.runtime.batch_export.set_mode = lambda mode="line": _show_batch_mode(ctx, mode)
    ctx.runtime.batch_export.set_mode("line")
