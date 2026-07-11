"""Redraw / selection-refresh orchestration extracted from ``WellViewerApp`` (A5).

``SelectionCoordinator`` bundles the two app-level orchestration surfaces that the
final god-object seams bottomed into:

- ``rebuild_all()`` — the master redraw fan-out after a data/selection change
  (invalidate stats cache → refresh sidebar map + Sample-Definitions groups →
  redraw bars/lines/scatter → per-tab follow-ups). The fan-out body is already
  ctx-native (``selection_controller.rebuild_all``), so the coordinator drives it
  directly.
- ``refresh_sidebar_map()`` — the debounced repaint of the line-graph well-picker's
  rep-colour map. The debounce is a ``QTimer`` plus a pending flag that lives on
  the main window (genuine Qt widget-shell state, cleared from several repaint
  paths), so this forwards to ``ctx.window`` — a widget need, not behaviour
  coupling.

The context holds this as the ``ctx.coordinator`` collaborator, replacing the last
two ``ctx.rebuild_all`` / ``ctx.refresh_sidebar_map`` frozen accessors so the
accessor-freeze set collapses to ``{"window"}`` — the terminal teardown state.
"""

from __future__ import annotations


class SelectionCoordinator:
    """Master redraw + sidebar-map refresh, held on the context as ``ctx.coordinator``."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def rebuild_all(self) -> None:
        """Run the master redraw fan-out after a data/selection change."""
        from well_viewer.core.selection_controller import rebuild_all
        rebuild_all(self._ctx)

    def refresh_sidebar_map(self) -> None:
        """Schedule the debounced sidebar rep-colour-map repaint (no-op if
        unavailable). The debounce (QTimer + pending flag) is widget-shell state
        on the main window, so this forwards through ``ctx.window``."""
        fn = getattr(self._ctx.window, "_refresh_sidebar_map", None)
        if callable(fn):
            fn()
