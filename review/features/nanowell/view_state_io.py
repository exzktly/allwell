"""Saving and restoring the Nanowells page with a Saved Plot (tracker V8a).

A saved Distribution figure that restored with another metric, baseline or axis, or without the nanowells
whose history sat beside it, would be a different figure presented as the saved one. The block is the page's
whole configuration (``NanowellState.to_config``), including the selected nanowells.

Reached through the registry's ``view_state_io`` hook, so ``persistence`` never names this feature and a build
without it stores nothing rather than erroring.
"""

from __future__ import annotations

from review.features.nanowell.controller import ensure_loaded, redraw


def snapshot_nanowell(ctx) -> dict:
    st = ctx.state.feature_slice("nanowell")
    return {} if st is None else st.to_config()


def apply_nanowell(ctx, block: dict) -> None:
    """Put a saved configuration back, then redraw (recomputing when the baseline changed)."""
    st = ctx.state.feature_slice("nanowell")
    if st is None or not isinstance(block, dict) or not block:
        return
    st.apply_config(block)
    refresh = ctx.widgets.get_or_none("_nw_refresh_controls")
    if callable(refresh):
        refresh()
    if ctx.widgets.get_or_none("_nw_canvas") is not None:
        ensure_loaded(ctx)
        redraw(ctx)
