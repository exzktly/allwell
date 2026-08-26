"""What the Heat Map is drawing, as a :class:`CellScope`.

This tab is the reason the export is defined as a *population plus markers*
rather than as the rows behind a drawn point. Its cells are not one statistic
over one row set: ``controller._cell_value`` dispatches on metric, and the
metrics disagree about what they are computed from. The count metric is a
pooled total over every well in the cell; the mean, fraction and ratio metrics
go through ``_repset_mean_of_means``, which is a mean of **per-well means** and
therefore not a function of a row set at all; and the mean-of-all metric uses a
different cell population again, with no per-channel ON filter.

So the file carries every cell of every well in the drawn layout, and the
``group`` column names the grid cell each row feeds. With that and
``well_label`` a reader can reproduce any of the metrics — including the mean of
means, which no single row set could have expressed. The scope block says which
one the figure drew.

The wells come from the layout rather than from the plot-group selection,
because the grid is a plate map: it draws every well it has a cell for. That
also means this tab has to have been drawn once before it can be exported —
``ctx.runtime.heatmap.cell_well_index`` is stamped by the redraw — which is the
same precondition the tab's summary export already has.
"""

from __future__ import annotations

from well_viewer.features.export.cell_scope_compute import CellScope, scope_from_ctx


def cell_scope(ctx) -> CellScope | None:
    """``None`` when the grid has not been drawn yet, so the caller can say so."""
    index = getattr(ctx.runtime.heatmap, "cell_well_index", None)
    if not index:
        return None

    well_paths = ctx.state.dataset.well_paths or {}
    cell_of: dict[str, str] = {}
    wells: list[tuple[str, str]] = []
    for (row, col), cell_wells in sorted(index.items()):
        for well in cell_wells:
            if well not in well_paths or well in cell_of:
                continue
            cell_of[well] = f"r{row}c{col}"
            wells.append((f"r{row}c{col}", well))

    from well_viewer.features.heatmap.controller import _selected_metric, _selected_tp

    tp = _selected_tp(ctx)
    timepoints = (float(tp),) if tp is not None else ()

    metric = str(_selected_metric(ctx) or "")
    return scope_from_ctx(
        ctx,
        plot="heatmap",
        timepoints=timepoints,
        include_gate=True,
        tp_fallback=False,
        wells=tuple(wells),
        notes=(
            ("drawn metric", metric),
            ("grid cells",
             "the group column names the (row, col) grid cell each row feeds; a "
             "cell may pool several wells"),
            ("averaging",
             "for the mean / fraction / ratio metrics a cell is the mean of its "
             "wells' means, not a pooled mean — group by well_label first"),
        ),
    )
