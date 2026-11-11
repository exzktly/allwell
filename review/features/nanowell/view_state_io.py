"""Saving and restoring the Nanowells page with a Saved Plot (tracker V8a).

A saved Distribution figure that restored with another metric, baseline or axis, or without the nanowells
whose history sat beside it, would be a different figure presented as the saved one. The block is the page's
whole configuration (``NanowellState.to_config``), including the selected nanowells and the *Subset* that drew it.

The one thing a restore leaves alone is the dataset's library of saved subsets (``saved_subsets``). The block
carries the library as it stood when the plot was saved, but the library is the user's own record of populations,
like the sample definitions a restore also never edits: replacing it with the old copy deleted every subset saved
after the plot, and brought back every one deleted since. The figure does not need it either, because the
*Subset* that drew it is stored by its conditions rather than by name; when it is no longer saved under a name the
page shows it as unsaved, which is what it now is.

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
    library = st.saved_subsets
    st.apply_config(block)
    st.saved_subsets = library
    refresh = ctx.widgets.get_or_none("_nw_refresh_controls")
    if callable(refresh):
        refresh()
    if ctx.widgets.get_or_none("_nw_canvas") is not None:
        ensure_loaded(ctx)
        redraw(ctx)
