"""EventBus → redraw wiring.

The bus's *redraw-only* signals — those whose only consequence is "the plots
need to refresh" — are routed through this coordinator. It owns no redraw
policy of its own: it forwards to ``redraw_scopes_or_defer``, the existing
coalescer in ``well_viewer.fold_change_scopes``. The bus only *triggers* a
redraw; the coalescer stays the *throttle* — redraw the visible scope now,
mark the rest dirty, flush on tab change.

Wired once at app construction by ``WellViewerApp.__init__``. Connections use
the default ``AutoConnection``, which is a synchronous ``DirectConnection``
for same-thread objects, so a signal redraws before ``emit()`` returns —
identical ordering to the direct ``redraw_scopes_or_defer`` calls this
replaces.

This is the load-bearing half of the EventBus (teardown Phase 3): before it,
every redraw-only emit was additive (emit *and* a direct redraw); now the
emit *is* the redraw trigger.
"""

from __future__ import annotations

from PySide6.QtCore import QObject


class RedrawCoordinator(QObject):
    """Subscribe the redraw-only bus signals to the scope coalescer.

    Constructed as a Qt child of the ``WellViewerApp`` (passed as the
    ``QObject`` parent), so Qt ownership keeps the coordinator — and its
    bound-method signal connections — alive without a ``self._*`` slot on the
    app.
    """

    def __init__(self, app, bus) -> None:
        super().__init__(app)
        self._app = app
        bus.channel_changed.connect(self._on_redraw_signal)

    def _on_redraw_signal(self, _state=None) -> None:
        """A redraw-only signal fired — coalesce a redraw of the visible scope."""
        from well_viewer.fold_change_scopes import redraw_scopes_or_defer
        redraw_scopes_or_defer(self._app)
