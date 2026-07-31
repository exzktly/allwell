"""Unified Scatter Plot tab — merges the per-cell and per-well aggregate views.

Two modes:

- ``cells`` — one matplotlib point per gated cell (intensity X vs intensity Y
  at a single timepoint).
- ``aggregate`` — one point per replicate or well (aggregated statistic on each
  axis, multi-timepoint).

Each page is built by the original ``build_scatter_cells_tab`` /
``build_scatter_agg_tab`` so the two rendering pipelines, controllers,
and per-mode controls stay separate. Only the active page is visible, so
controls that don't apply to the other mode aren't shown.

The mode is chosen from the ``View`` toggle in the shared control band, not
from a tab bar inside this page: a tab strip here was a third row of tabs under
the pane's tab row and the plot-type row, and what it selects is the scope of
what gets plotted, which is what the band's left zone is for. The page
therefore holds a bare page stack and nothing else. ``ctx.runtime.scatter``
carries the ``set_mode`` callback the band drives, and ``ctx.state.scatter.mode``
stays the record of which mode is live.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from well_viewer.core.centre_view import NamedPageStack
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

    pages = NamedPageStack(parent)
    layout.addWidget(pages, 1)

    cells_page = QWidget()
    QVBoxLayout(cells_page).setContentsMargins(0, 0, 0, 0)
    build_scatter_cells_tab(ctx, cells_page)
    pages.addPage(_MODE_CELLS, cells_page)

    agg_page = QWidget()
    QVBoxLayout(agg_page).setContentsMargins(0, 0, 0, 0)
    build_scatter_agg_tab(ctx, agg_page)
    pages.addPage(_MODE_AGG, agg_page)

    # A8 burn-down: the page-stack handle goes through the WidgetRegistry
    # (``ctx.widgets``), which stamps ``app._scatter_pages``. The active mode
    # is typed state (``ctx.state.scatter.mode``); the set-mode callback stays.
    ctx.widgets.register("_scatter_pages", pages)

    def _set_mode(mode: str) -> None:
        mode = _MODE_AGG if mode == _MODE_AGG else _MODE_CELLS
        pages.setCurrentByName(mode)
        ctx.state.scatter.mode = mode
        # Mirror the band's View toggle. The mode is also set from outside the
        # toggle — restoring a saved view calls this directly — and a toggle
        # left showing the other mode is worse than no toggle. Reached through
        # the widget registry so the feature still does not import the shell.
        toggle = ctx.widgets.get_or_none("_plotting_scatter_view")
        if toggle is not None:
            blocked = toggle.blockSignals(True)
            try:
                toggle.setCurrentByData(mode)
            finally:
                toggle.blockSignals(blocked)
        try:
            update_scatter_menus(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
        redraw = (redraw_scatter_agg_orchestrate if mode == _MODE_AGG
                  else redraw_scatter_orchestrate)
        try:
            redraw(ctx)
        except Exception:
            swallowed()  # was: pass (silent except)

    ctx.runtime.scatter.set_mode = _set_mode
    # Adopt whichever mode a restored session left in state, so the page and the
    # band's toggle agree from the first paint.
    pages.setCurrentByName(ctx.state.scatter.mode or _MODE_CELLS)


def scatter_active_mode(ctx) -> str:
    """Return ``"cells"`` or ``"aggregate"``; defaults to ``"cells"``."""
    return ctx.state.scatter.mode or _MODE_CELLS


def scatter_redraw_active(ctx) -> None:
    """Redraw whichever scatter mode is currently visible."""
    if scatter_active_mode(ctx) == _MODE_AGG:
        redraw_scatter_agg_orchestrate(ctx)
    else:
        redraw_scatter_orchestrate(ctx)
