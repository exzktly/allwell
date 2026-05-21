"""The viewer's dependency-injection context.

``ViewerContext`` is the single typed handle that tab builders and controllers
receive instead of reaching into the ``WellViewerApp`` god-object. It bundles
the typed domain state, the event bus, and the widget registry.

The ``app`` field is **transitional**: most builders and controllers still call
``WellViewerApp`` methods that have not yet been extracted, so ``ctx.app`` is the
explicit, visible escape hatch for that residual coupling. As methods migrate
out, ``ctx.app`` usage shrinks and the field is eventually removed — the same
strangler pattern as the Phase 5 ``__getattr__`` forwarder.

Frozen: a context is wired once at construction and never reassigned.
"""

from __future__ import annotations

from dataclasses import dataclass

from well_viewer.state import AppState, EventBus
from well_viewer.views.widget_registry import WidgetRegistry


@dataclass(frozen=True)
class ViewerContext:
    """Typed dependency bundle injected into tab builders and controllers.

    Consumers read ``ctx.state.<domain>`` for typed state, ``ctx.bus`` for the
    event bus, ``ctx.widgets`` for widget handles, and — transitionally —
    ``ctx.app`` for behaviour not yet extracted from ``WellViewerApp``.
    """

    app: object          # transitional: the WellViewerApp instance
    state: AppState
    bus: EventBus
    widgets: WidgetRegistry
