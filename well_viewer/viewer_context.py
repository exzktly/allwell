"""The viewer's dependency-injection context.

``ViewerContext`` is the single typed handle that tab builders and controllers
receive instead of reaching into the ``WellViewerApp`` god-object. It bundles
the typed domain state, the event bus, and the widget registry.

The ``app`` field is **transitional**: most builders and controllers still call
``WellViewerApp`` methods that have not yet been extracted, so the ``app`` field
is the explicit, visible escape hatch for that residual coupling. As methods
migrate out, reach-throughs shrink and the field is eventually removed — the
same strangler pattern as the Phase 5 ``__getattr__`` forwarder.

**Named service accessors (A8 Phase 3, design "C").** Some app touches are not
lazy reach-throughs but *genuine app/window-level services* (the status bar, the
Save-As dialog, …). Rather than leave those scattered across features as raw
``app``-field method calls, they are exposed here as small, named methods
(``ctx.set_status`` / ``ctx.save_figure_dialog`` / …). This makes the surface a
feature legitimately needs from ``WellViewerApp`` **enumerable in one place** —
the method list below *is* the spec for the eventual god-object teardown.

Anti-gaming rule: these are a *minimal, closed* set — never a ``__getattr__``
passthrough. Every accessor is frozen by ``tests/test_ctx_service_accessors.py``
so adding one is a deliberate, reviewed act; if the list grows past a handful,
that is the signal to decompose ``WellViewerApp`` itself, not to add more.

Frozen: a context is wired once at construction and never reassigned.
"""

from __future__ import annotations

from dataclasses import dataclass

from well_viewer.core.app_state import AppState
from well_viewer.core.event_bus import EventBus
from well_viewer.core.widget_registry import WidgetRegistry


@dataclass(frozen=True)
class ViewerContext:
    """Typed dependency bundle injected into tab builders and controllers.

    Consumers read ``ctx.state.<domain>`` for typed state, ``ctx.bus`` for the
    event bus, ``ctx.widgets`` for widget handles, and — transitionally — the
    ``app`` field for behaviour not yet extracted from ``WellViewerApp``.
    """

    app: object          # transitional: the WellViewerApp instance
    state: AppState
    bus: EventBus
    widgets: WidgetRegistry

    # ── Named app/window-service accessors (A8 Phase 3, design "C") ──────────
    # Minimal, closed set — see the module docstring and the freeze test. Each
    # wraps one genuine WellViewerApp service so feature code names it instead of
    # reaching through the ``app`` escape hatch.

    def set_status(self, message: str) -> None:
        """Show *message* in the main-window status bar (no-op if unavailable)."""
        fn = getattr(self.app, "_set_status", None)
        if callable(fn):
            fn(message)

    def save_figure_dialog(self, fig, default_name: str) -> None:
        """Open the app's Save-As dialog for *fig*, honouring the shared file-type
        filter and the active dataset directory."""
        self.app._save_matplotlib_fig(fig, default_name)

    def open_export_style_panel(self, plot_key: str) -> None:
        """Open the figure-properties (export-style) side panel for *plot_key*."""
        self.app._open_export_style_panel(plot_key)
