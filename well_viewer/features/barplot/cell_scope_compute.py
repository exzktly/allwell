"""What the Bar tab is drawing, as a :class:`CellScope`.

Bar is the tab with three row-set producers — per-well, grouped, and FOV-pooled
(``collect.py``) — and they do not agree: on a missing gate-channel column the
aggregation drops the whole well while the FOV-pooled collector keeps it and
skips that gate. Under the export's rule that divergence needs no resolution.
The rows are the tab's cell population either way; the disagreement shows up as
``plot_in_population = 0`` for that well, and the scope block records that a
gate channel was missing.

The violin and beeswarm overlays are the reason the exported set is wider than
the aggregation's. They draw from the inclusion mask, a timepoint match and a
finite value — **no ``area_px`` check and no per-channel cutoffs**
(``renderer.py:213``, ``:380``) — which is exactly the population this module
exports. So the file holds what the overlay draws, and ``plot_in_population``
marks the narrower set the bars were computed from.

This module is a ``_compute`` leaf rather than ``collect.py`` because
``barplot/collect.py`` already exists and owns the drawing-side collection; the
same name is used in every feature package that contributes a scope.
"""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_cutoff_for_column
from well_viewer.domain.stats_compute import _thresh_on_col
from well_viewer.features.export.cell_scope_compute import CellScope, scope_from_ctx


def cell_scope(ctx) -> CellScope:
    from well_viewer.features.barplot.renderer import resolve_bar_timepoints

    val_col = ctx.state.channel.active_val_col
    try:
        timepoints = tuple(float(t) for t, _label in resolve_bar_timepoints(ctx))
    except (TypeError, ValueError):
        timepoints = ()

    overlay_on = bool(ctx.state.bar.violin or ctx.state.bar.swarm)
    drawn = (
        "every exported row (the violin/beeswarm overlay is drawing them)"
        if overlay_on else "plot_in_population=1"
    )
    return scope_from_ctx(
        ctx,
        plot="bar",
        val_col=val_col,
        threshold=float(on_cutoff_for_column(ctx, val_col)),
        timepoints=timepoints,
        include_gate=True,
        tp_fallback=True,
        on_col=_thresh_on_col(ctx),
        notes=(
            ("drawn statistic",
             "bar height is the mean of the value column over "
             "plot_above_threshold=1 within plot_in_population=1"),
            ("overlay", drawn),
            ("replicate sets",
             "a set's bar is the mean of its wells' means with a cross-well SD "
             "(ddof=1); group by well_label to reproduce it"),
        ),
    )
