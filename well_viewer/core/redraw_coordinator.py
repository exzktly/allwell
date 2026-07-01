"""EventBus → redraw wiring.

The redraw consequence of the bus signals is routed through this coordinator:
``channel_changed`` and ``plot_options_changed`` (whose only consequence is "the
plots need to refresh") and the redraw part of ``dataset_loaded``. The
coordinator owns no redraw *policy* — for the redraw-only signals it forwards to
``redraw_scopes_or_defer``, the existing coalescer in
``well_viewer.fold_change_scopes`` (the bus only *triggers* a redraw; the
coalescer stays the *throttle* — redraw the visible scope now, mark the rest
dirty, flush on tab change). ``dataset_loaded`` instead drives a full
``_redraw`` because a freshly loaded dataset invalidates every plot.

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
        bus.plot_options_changed.connect(self._on_redraw_signal)
        bus.dataset_loaded.connect(self._on_dataset_loaded)

    def _on_redraw_signal(self, _state=None) -> None:
        """A redraw-only signal fired — coalesce a redraw of the visible scope."""
        from well_viewer.domain.fold_change_scopes import redraw_scopes_or_defer
        redraw_scopes_or_defer(self._app)

    def _on_dataset_loaded(self, _state=None) -> None:
        """A dataset finished loading — redraw every plot.

        ``_redraw`` (``redraw_app``) covers the default-scope plots
        (line / distribution / heat map); the Bar / Scatter plots redraw
        through their own scope callables, which ``_redraw`` does not cover. So
        after ``_redraw`` we draw the visible non-default scope and defer the
        rest — otherwise a freshly loaded dataset leaves the (now default) Bar
        Plots tab as an empty white axis until the user touches a control.
        Passing ``_redraw``'s callable as ``default_redraw`` keeps the line
        scope from being drawn twice when the line tab is the visible one.
        """
        self._app._redraw()
        from well_viewer.domain.fold_change_scopes import (
            get_scope, redraw_scopes_after_load,
        )
        line_scope = get_scope("line")
        redraw_scopes_after_load(
            self._app,
            default_redraw=line_scope.redraw if line_scope is not None else None,
        )
