"""User-facing notification services extracted from ``WellViewerApp`` (A5).

``NotificationService`` bundles the two window-shell notification surfaces the app
exposes to feature code — the status-bar line (``set_status``) and the transient
toast overlay (``toast``). It replaces the ``ctx.set_status`` / ``ctx.toast`` frozen
accessors that reached into the private ``_window``: the context now *holds* this
service (``ctx.notify``) as a collaborator instead of naming god-object methods.

The bodies stay thin because the underlying operations are genuinely Qt-shell ops
with no typed-state home: ``set_status`` writes the status label, ``toast`` calls
the ctx-native ``ui_helpers.toast`` overlay helper. The service is the enumerable
home for them, so ``ViewerContext`` no longer needs a method per surface and the
accessor-freeze set shrinks toward ``{"window"}``.
"""

from __future__ import annotations


class NotificationService:
    """Status-bar + toast notifications, held on the context as ``ctx.notify``."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def set_status(self, message: str) -> None:
        """Show *message* in the main-window status bar (no-op if unavailable)."""
        fn = getattr(self._ctx.window, "_set_status", None)
        if callable(fn):
            fn(message)

    def toast(self, message: str, *, kind: str = "info", msec: int = 3500) -> None:
        """Show a transient notification toast (no-op if unavailable).

        A self-dismissing popup announcing an action's outcome (import/export
        success, a failure), distinct from the persistent status line."""
        fn = getattr(self._ctx.window, "_toast", None)
        if callable(fn):
            fn(message, kind=kind, msec=msec)
