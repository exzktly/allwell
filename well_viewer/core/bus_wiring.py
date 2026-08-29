"""Central wiring for the EventBus subscribers.

A single place that connects every bus subscriber, called once from
``WellViewerApp.__init__``. As the teardown migrates more behaviour onto the
bus (Phase 3 and beyond) new coordinators are added here, not scattered
through ``runtime_app.py`` — so the composition root stays a one-line call.
"""

from __future__ import annotations


def wire_event_bus(win, bus) -> None:
    """Construct and connect every EventBus subscriber for *win*.

    Each coordinator is a ``QObject`` child of *win*, so Qt ownership keeps the
    subscribers — and their signal connections — alive for the win's lifetime
    without a ``self._*`` slot.
    """
    from well_viewer.core.redraw_coordinator import RedrawCoordinator
    from well_viewer.core.selection_controller import SelectionRefreshCoordinator
    from well_viewer.core.scope_registry import register_default_scopes

    RedrawCoordinator(win, bus)
    SelectionRefreshCoordinator(win, bus)
    register_default_scopes(win)
