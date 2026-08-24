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


def report_verdict_failures(ctx, *, layer: str, column: str,
                            failed: list[str] | None) -> str:
    """Say on screen which wells' inclusion verdict could not be computed.

    :func:`well_viewer.data.cache_projectors.project_verdict_column` returns the
    labels whose *column* it had to drop, and dropping it is the honest choice —
    every consumer reads an absent verdict as all-pass, the same as a frame that
    predates the feature. Honest, but invisible: the plate keeps drawing, the
    numbers move, and those wells are now filtered by nothing at all while the
    QC or Crop controls on screen say they are filtered by whatever the user
    just set. This is the one call that turns that into something the user can
    see.

    A module-level function rather than a :class:`NotificationService` method
    because the two callers (``qc.controller.recompute_qc_all`` and
    ``crop.controller.recompute_crop_all``) must not drift into two different
    sentences for one situation. A ``ctx`` with no notification service — a
    stub, or a headless caller — is a silent no-op; the failure is already in
    the log by the time this runs.

    **Returns the message** (empty string on a clean pass) as well as showing
    it. The status line is a single slot, and a caller that writes its own
    outcome line immediately afterwards — every crop action does — would erase
    this one within the same tick. Those callers append the returned string to
    their own message instead, so the warning survives the very actions most
    likely to produce it. The toast is raised here regardless, because it is the
    surface that cannot be overwritten by the next line of the same handler.
    """
    if not failed:
        return ""
    wells = ", ".join(sorted(str(w) for w in failed))
    msg = (
        f"WARNING — {layer}: {column} could not be computed for "
        f"{len(failed)} well(s) ({wells}). Those wells are now filtered by no "
        f"{layer} rule at all — every one of their cells counts — while the "
        "controls still show the settings you set. See the log for the reason."
    )
    notify = getattr(ctx, "notify", None)
    if notify is None:
        return msg
    fn = getattr(notify, "set_status", None)
    if callable(fn):
        fn(msg)
    fn = getattr(notify, "toast", None)
    if callable(fn):
        fn(msg, kind="error", msec=8000)
    return msg
