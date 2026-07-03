"""Unified Scatter Plot tab — merges the per-cell and per-well aggregate views.

Two sub-tabs:

- ``Per-cell points`` — one matplotlib point per gated cell (intensity X
  vs intensity Y at a single timepoint).
- ``Per-well aggregate`` — one point per replicate or well (aggregated
  statistic on each axis, multi-timepoint).

Each page is built by the original ``build_scatter_cells_tab`` /
``build_scatter_agg_tab`` so the two rendering pipelines, controllers,
and per-mode controls stay separate. Only the active page is visible, so
controls that don't apply to the other mode aren't shown.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QTabWidget, QVBoxLayout, QWidget,
)

from well_viewer.features.scatter.agg_tab_view import build_scatter_agg_tab
from well_viewer.features.scatter.cells_tab_view import build_scatter_cells_tab
from well_viewer.features.scatter.controller import redraw_scatter_agg_orchestrate
from well_viewer.features.scatter.controller import redraw_scatter_orchestrate
from well_viewer.features.scatter.controller import update_scatter_menus
from well_viewer.core.safe import swallowed


_MODE_CELLS = "cells"
_MODE_AGG = "aggregate"


def build_scatter_tab(ctx, parent: QWidget) -> None:
    """Construct the unified Scatter Plot tab inside *parent*."""
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    tab_widget = QTabWidget(parent)
    tab_widget.setObjectName("ScatterSubTabs")
    tab_widget.setUsesScrollButtons(False)
    tab_widget.setElideMode(Qt.ElideNone)
    tab_widget.tabBar().setUsesScrollButtons(False)
    tab_widget.tabBar().setElideMode(Qt.ElideNone)
    tab_widget.tabBar().setExpanding(False)
    layout.addWidget(tab_widget, 1)

    cells_page = QWidget()
    QVBoxLayout(cells_page).setContentsMargins(0, 0, 0, 0)
    build_scatter_cells_tab(ctx, cells_page)
    tab_widget.addTab(cells_page, "Per-cell points")

    agg_page = QWidget()
    QVBoxLayout(agg_page).setContentsMargins(0, 0, 0, 0)
    build_scatter_agg_tab(ctx, agg_page)
    tab_widget.addTab(agg_page, "Per-well aggregate")

    # A8 burn-down: the sub-tab QTabWidget handle goes through the WidgetRegistry
    # (``ctx.widgets``), which stamps ``app._scatter_tab_widget``. The active-mode
    # is typed state (``ctx.state.scatter.mode``); the set-mode callback stays.
    ctx.widgets.register("_scatter_tab_widget", tab_widget)

    def _set_mode(mode: str) -> None:
        if mode == _MODE_AGG:
            if tab_widget.currentIndex() != 1:
                tab_widget.setCurrentIndex(1)
            ctx.state.scatter.mode = _MODE_AGG
            try:
                update_scatter_menus(ctx.app)
            except Exception:
                swallowed()  # was: pass (silent except)
            try:
                redraw_scatter_agg_orchestrate(ctx.app)
            except Exception:
                swallowed()  # was: pass (silent except)
        else:
            if tab_widget.currentIndex() != 0:
                tab_widget.setCurrentIndex(0)
            ctx.state.scatter.mode = _MODE_CELLS
            try:
                update_scatter_menus(ctx.app)
            except Exception:
                swallowed()  # was: pass (silent except)
            try:
                redraw_scatter_orchestrate(ctx.app)
            except Exception:
                swallowed()  # was: pass (silent except)
    ctx.runtime.scatter.set_mode = _set_mode

    tab_widget.currentChanged.connect(
        lambda idx: _set_mode(_MODE_AGG if idx == 1 else _MODE_CELLS)
    )


def scatter_active_mode(app) -> str:
    """Return ``"cells"`` or ``"aggregate"``; defaults to ``"cells"``."""
    return app._app_state.scatter.mode or _MODE_CELLS


def scatter_redraw_active(app) -> None:
    """Redraw whichever scatter mode is currently visible."""
    if scatter_active_mode(app) == _MODE_AGG:
        redraw_scatter_agg_orchestrate(app)
    else:
        redraw_scatter_orchestrate(app)
