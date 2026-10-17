"""Hold every plot while a dataset loads, and through the gating pass the load starts.

Opening a dataset used to draw its plots several times over. The channel
refresh inside ``recalculate_threshold`` drew Scatter, Scatter Aggregate,
Response and the Heat Map; the ``dataset_loaded`` redraw drew Line, Distribution
and the Heat Map again; and when the load had started a Cell Gating pass (a
saved non-default gate), ``_cell_gating_on_finished`` drew them all a third time
with the gates applied. Only that last drawing was of the numbers the user
asked for, and on a plate of large wells each one cost a pass over every well
(``_Docs/Markdowns/REREAD_TRACKER.md``, R6; the reporter's decision: do not draw
twice).

So a load holds the plots. While ``PlotState.held_for`` is set, every redraw
chokepoint (``plot_orchestrator.redraw``, ``fold_change_scopes.
redraw_scopes_or_defer`` / ``redraw_scopes_after_load`` / ``flush_dirty_scopes``)
marks every scope dirty and draws nothing. At the end of the load:

* with no gating pass started, the hold is lifted just before
  ``dataset_loaded`` is emitted, and that emit draws the plate exactly as it
  always did — the draws it suppressed along the way were all superseded by it;
* with a gating pass started, the hold becomes ``"gating"``, every plot shows
  the :data:`~review.core.ui_helpers.GATING_IN_PROGRESS` placeholder rather than
  the previous dataset's figure or this one's ungated numbers, and
  :func:`release_after_gating` lifts it when the pass finishes or fails and
  draws the visible plot once; every other plot is drawn when it is next shown.

The feature redraw entry points that do not pass through those chokepoints —
Bar, Scatter, Scatter Aggregate, Response, Distribution and the Heat Map's own
``redraw_*`` — return early while held, for the same reason.

A pass the user restarts by editing a gate is cancelled without a signal and
replaced, so the replacement's end lifts the hold. A pass cancelled by
``cell_gating_recompute_now`` has no asynchronous replacement, so that function
lifts the hold itself once its synchronous pass ends. A pass that belongs to a
previous dataset is cancelled by that dataset's swap and emits nothing.
"""

from __future__ import annotations

HOLD_LOADING = "loading"
HOLD_GATING = "gating"


def _plot(ctx):
    """The context's ``PlotState``, or ``None`` for a context too thin to have one."""
    return getattr(getattr(ctx, "state", None), "plot", None)


def hold(ctx, why: str) -> None:
    """Hold every plot for *why* (:data:`HOLD_LOADING` / :data:`HOLD_GATING`)."""
    plot = _plot(ctx)
    if plot is not None:
        plot.held_for = why


def release(ctx) -> None:
    """Lift any hold. Draws nothing: the caller decides what to draw."""
    plot = _plot(ctx)
    if plot is not None:
        plot.held_for = ""


def held_for(ctx) -> str:
    return getattr(_plot(ctx), "held_for", "")


def release_after_gating(ctx) -> bool:
    """Lift a load's gating hold and draw the visible tab once. ``False`` when there was none.

    Every plot tab's activation hook draws its figure, so re-running the
    activation of the tab on screen (``tab_controller.on_tab_change``, which the
    load itself runs at its end for the same reason) draws exactly what the user
    is looking at. Every other plot draws when it is next shown: its activation
    hook runs then, and the registered scopes the hold marked dirty are flushed
    by the same tab change.
    """
    if held_for(ctx) != HOLD_GATING:
        return False
    release(ctx)
    from review.core.tab_controller import on_tab_change
    from review.core.ui_helpers import refresh_empty_state_pages

    refresh_empty_state_pages(ctx.window)
    on_tab_change(ctx)
    return True
