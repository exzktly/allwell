"""Observations for the plot tools: what a plot's points are on this dataset, and the rows behind them.

Cells come from the row cache, as they always have. Nanowells come from whichever feature answers the
``observations.nanowell`` registry service (the Nanowells feature, ``review/domain/observation.py``) — asked, never
imported, so a build without that feature simply has no nanowell observations, and every caller here decides what
that means instead of meeting an ``ImportError``.

Imports no feature.
"""

from __future__ import annotations

from review.domain.observation import CELLS, NANOWELL_SERVICE, NANOWELLS, ObservationTable


def is_nanowell_dataset(ctx) -> bool:
    """Whether the loaded dataset was written by a nanowell run."""
    from review.domain.qc_params import is_nanowell_run

    return is_nanowell_run(getattr(getattr(ctx.state, "dataset", None), "pipeline_info", None))


def nanowell_views_available(ctx) -> bool:
    """Whether this build has a supplier of nanowell observations (the Nanowells feature)."""
    features = getattr(ctx, "features", None)
    return features is not None and features.service(NANOWELL_SERVICE) is not None


def available_observations(ctx) -> tuple[str, ...]:
    """The observations a plot may draw on this dataset in this build: cells, and nanowells when both hold."""
    if is_nanowell_dataset(ctx) and nanowell_views_available(ctx):
        return (CELLS, NANOWELLS)
    return (CELLS,)


def nanowell_observations(ctx, wells) -> ObservationTable | None:
    """The nanowell rows of *wells*, or ``None`` when this build or this dataset has none."""
    if not is_nanowell_dataset(ctx):
        return None
    return ctx.features.query(NANOWELL_SERVICE, ctx, list(wells), default=None)


def occupied_nanowells_by_well(ctx, wells) -> dict[str, int]:
    """Per well, how many distinct nanowell addresses its cell rows name — the nanowells holding a cell.

    Read from the cell table alone, so it needs no nanowell feature: it is what a build without one can still say
    about a nanowell plate's cell plots (``observation.pooled_cells_line``).
    """
    from review.data.data_loading import get_rows

    out: dict[str, int] = {}
    for well in wells:
        if well not in ctx.state.dataset.well_paths:
            continue
        rows = get_rows(ctx, well)
        if rows is None or "nw_q" not in rows.columns or "nw_r" not in rows.columns:
            continue
        addressed = rows[["nw_q", "nw_r"]].dropna()
        out[well] = int(len(addressed.drop_duplicates()))
    return out


def observation_groups(ctx) -> list[tuple[str, tuple[str, ...], str]]:
    """``(legend label, wells, colour)`` per plotted member: the replicate sets when any are active, else each
    selected well — the members every plot draws, in their order (as ``scatter.collect.scatter_groups``)."""
    from review.domain.plot_members import plot_replicate_sets
    from review.domain.plot_order import apply_order
    from review.domain.selections_model import (
        color_for_replicate_set, color_for_well, replicate_display_label, well_display_label,
    )
    from review.domain.viewer_state import parse_rc

    paths = ctx.state.dataset.well_paths
    rsets = plot_replicate_sets(ctx)
    if rsets:
        rsets = apply_order(rsets, list(ctx.state.plot.line_order_rsets or []), key=lambda r: getattr(r, "name", ""))
        return [(replicate_display_label(ctx, r), tuple(w for w in r.wells if w in paths),
                 color_for_replicate_set(r, ctx.state.selection.selections)) for r in rsets]
    selected = sorted((w for w in ctx.state.selection.selected_wells if w in paths), key=parse_rc)
    selected = apply_order(selected, list(ctx.state.plot.line_order_wells or []), key=lambda x: x)
    return [(well_display_label(ctx, w), (w,), color_for_well(w)) for w in selected]
