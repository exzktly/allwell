"""The scatter tabs' multi-timepoint picker — one implementation, two tabs.

Both scatter sub-tabs (per-cell and aggregate) pick their timepoints on the
same page of the inspector (``review.core.inspector_pages.open_multi_select``),
differing only in which selection dict they mutate, which button opens the
page, and which pair of callbacks they fire.

No business logic: it reads and writes a selection dict the caller owns and
calls back into the caller's controller.
"""

from __future__ import annotations

from collections.abc import Callable



def open_timepoint_selector(
    ctx,
    *,
    selections: dict[str, bool],
    button_name: str,
    refresh_display: Callable,
    redraw: Callable,
) -> None:
    """Show a tab's timepoints as a multi-select page of the inspector.

    *selections* is the live ``{tp_string: bool}`` dict the tab owns; it is
    mutated in place, so the caller's state is the single source of truth.
    *refresh_display* and *redraw* are the tab's own ``(ctx) -> None`` callbacks,
    run once after every change — a tick, All, or None.
    """
    from review.core.inspector_pages import open_multi_select

    order = sorted(selections.keys(), key=float)

    def _apply(chosen: list) -> None:
        picked = set(chosen)
        for tp in list(selections):
            selections[tp] = tp in picked
        refresh_display(ctx)
        redraw(ctx)

    open_multi_select(ctx, ctx.widgets.get_or_none(button_name), key=f"{button_name}/timepoints",
                      title="Timepoints", items=order,
                      checked=[tp for tp in order if selections[tp]], on_change=_apply,
                      empty_text="No timepoints in this dataset.")


