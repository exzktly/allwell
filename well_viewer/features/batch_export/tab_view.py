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

from well_viewer.core.lazy import lazy as _lazy

#: The Response mode's panel, resolved on first use. ``_offered_modes`` has
#: already established that the Response Curves feature is in this build before
#: anything calls it.
_build_response_panel = _lazy(
    "well_viewer.features.batch_export.response_panel",
    "build_response_batch_panel")


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


#: The page's modes, in bar order, paired with the key each one dispatches on
#: and the Plotting leaf each one needs in the build. The bar carries no user
#: data of its own — a ``QTabBar`` indexes by position — so the order of the
#: *offered* modes IS the mapping, which is why ``_offered_modes`` is asked for
#: the list everywhere rather than this tuple being read directly.
#:
#: The feature name is what keeps a mode out of a build that cannot draw it.
#: Response mode reuses Response Curves' own collector and renderer, so a
#: profile that drops that leaf has no code to run — and a mode bar offering it
#: anyway would build a panel whose Run button raises. ``None`` means the mode
#: needs nothing beyond Batch Export itself, which is true of the four that
#: draw their figures from collectors this package already reaches.
_MODES = (
    ("Line", "line", None),
    ("Bar", "bar", None),
    ("Scatter (cells)", "scatter_cells", None),
    ("Scatter (aggregate)", "scatter_agg", None),
    ("Response", "response", "Response Curves"),
)

#: The mode the page opens on, and what an unrecognised key falls back to. Line
#: is always offered, so this is never a key that is not in the bar.
_DEFAULT_MODE = "line"


def _offered_modes(ctx) -> tuple[tuple[str, str], ...]:
    """``(label, key)`` for the modes this build can actually run.

    Derived from the feature registry rather than from a second list somebody
    maintains: a mode kept in the bar after its feature was profiled out is not
    a cosmetic mistake, it is a tab that builds and a Run button that raises.
    """
    return tuple(
        (label, key) for label, key, feature in _MODES
        if feature is None or ctx.features.get(feature) is not None
    )


def _refresh_mode_buttons(ctx, mode: str) -> None:
    """Sync the mode bar's selected tab with the active builder."""
    state = ctx.runtime.batch_export.inline_state
    if not state:
        return
    bar = state.get("mode_bar")
    if bar is None:
        return
    modes = state.get("modes") or _offered_modes(ctx)
    idx = next((i for i, (_lbl, key) in enumerate(modes) if key == mode), -1)
    if idx < 0 or bar.currentIndex() == idx:
        return
    blocked = bar.blockSignals(True)
    try:
        bar.setCurrentIndex(idx)
    finally:
        bar.blockSignals(blocked)


def _build_mode_panel(ctx, mode: str, host):
    """The panel for *mode*. Batch Export defines its OWN export groups (the
    left-panel editor) and deliberately does NOT default to the sidebar
    replicate sets — replicate groups are not the right unit for batch exports
    (reverts N10)."""
    if mode == "bar":
        return BarBatchExportPanel(ctx, host, use_sidebar_groups=False)
    if mode == "scatter_cells":
        return ScatterBatchExportPanel(
            ctx, host, scatter_mode="cells", use_sidebar_groups=False)
    if mode == "scatter_agg":
        return ScatterBatchExportPanel(
            ctx, host, scatter_mode="aggregate", use_sidebar_groups=False)
    if mode == "response":
        # Through a proxy rather than an import statement: the panel module
        # reaches Response Curves' collector and renderer, and there is no
        # reason to load either in a session that never opens the mode.
        return _build_response_panel(ctx, host)
    return BatchExportPanel(ctx, host, use_sidebar_groups=False)


def _show_batch_mode(ctx, mode: str) -> None:
    state = ctx.runtime.batch_export.inline_state
    if not state:
        return

    offered = {key for _lbl, key in (state.get("modes") or _offered_modes(ctx))}
    if mode not in offered:
        # Includes a mode this build does not offer — a saved view naming
        # ``response`` in a profile without Response Curves, say. Falling back
        # is right; falling back *silently to a mode that is in the bar* is what
        # keeps the bar and the panel agreeing, which a raise here would not.
        mode = _DEFAULT_MODE

    host = state["host"]
    host_layout = host.layout()
    panels = state["panels"]
    if mode not in panels:
        panel = _build_mode_panel(ctx, mode, host)
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
    # sample definitions on every tab show. Batch-export groups are export-
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
    modes = _offered_modes(ctx)
    mode_bar = QTabBar()
    mode_bar.setObjectName("PlottingSubTabs")
    mode_bar.setExpanding(False)
    mode_bar.setElideMode(Qt.ElideNone)
    mode_bar.setUsesScrollButtons(True)
    mode_bar.setDrawBase(False)
    for label, _key in modes:
        mode_bar.addTab(label)
    from well_viewer.core.app_shell_view import register_page_tabs
    register_page_tabs(ctx.window, "Batch Export", mode_bar)

    from well_viewer.core.plot_orchestrator import (
        open_batch_export_shim,
        open_bar_batch_export_shim,
        open_response_batch_export_shim,
        open_scatter_cells_batch_export_shim,
        open_scatter_agg_batch_export_shim,
    )

    _mode_dispatch = {
        "line": open_batch_export_shim,
        "bar": open_bar_batch_export_shim,
        "scatter_cells": open_scatter_cells_batch_export_shim,
        "scatter_agg": open_scatter_agg_batch_export_shim,
        "response": open_response_batch_export_shim,
    }

    def _on_mode_changed(idx: int) -> None:
        mode = modes[idx][1] if 0 <= idx < len(modes) else _DEFAULT_MODE
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
        "mode": _DEFAULT_MODE,
        "mode_bar": mode_bar,
        "modes": modes,
        "band": band,
    }
    ctx.runtime.batch_export.set_mode = (
        lambda mode=_DEFAULT_MODE: _show_batch_mode(ctx, mode))
    ctx.runtime.batch_export.set_mode(_DEFAULT_MODE)
