"""Heat Map under **Observations: Nanowells**: the plate grid coloured by each cell's wells' nanowell medians
(NANOWELL_OBSERVATIONS_TRACKER #15).

A grid cell holds one well or a replicate set's wells; its value is the mean over those wells of each well's median
of the band's nanowell metric at the heat map's timepoint, and its ``±`` is the SEM over wells (none for one well) —
wells are the replicates; nanowells are never pooled across wells. The hexagonal array layout stays on Nanowells ▸
Array, a QC and imaging view; this grid is the plate. Rows come from the Nanowells feature through
``review/data/observations``, never an import.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from review.domain.observation import NANOWELLS

LEAF = "Heat Map"


@dataclass
class NanowellGrid:
    arr: np.ndarray
    err: np.ndarray
    label: str
    vmin: float | None
    vmax: float | None
    footer: str


def nanowell_grid(ctx, layout, tp: float, expand) -> NanowellGrid | None:
    """The grid's values under Nanowells, or ``None`` when the Heat Map draws cells. *expand* maps a cell's wells to
    the wells they stand for (a replicate set's members)."""
    from review.core.observation_band import chosen_metric, effective_observations, nanowell_table_for
    from review.domain.observation import footer_line, group_points, well_medians

    if effective_observations(ctx, LEAF) != NANOWELLS:
        return None
    cells = {rc: [w for w in expand(wells) if w in ctx.state.dataset.well_paths] for rc, wells in layout.cells.items()}
    every = sorted({w for ws in cells.values() for w in ws})
    table = nanowell_table_for(ctx, every)
    arr = np.full((layout.rows, layout.cols), np.nan)
    err = np.full((layout.rows, layout.cols), np.nan)
    if table is None:
        return NanowellGrid(arr, err, "", None, None, "Observations: nanowells · no nanowell tables for these wells")
    key = chosen_metric(ctx, table)
    metric = table.metric(key)
    all_medians = well_medians(table, key, every)
    here = well_medians(table, key, every, [tp])
    for (r, c), wells in cells.items():
        if not (0 <= r < layout.rows and 0 <= c < layout.cols) or not wells:
            continue
        points = group_points(here, [("cell", wells)])
        if points:
            arr[r, c], err[r, c] = points[0].mean, points[0].sem
    finite = all_medians["median"].dropna()
    vmin, vmax = (float(finite.min()), float(finite.max())) if len(finite) else (None, None)
    used, total = int(here["n"].sum()) if len(here) else 0, int(here["total"].sum()) if len(here) else 0
    footer = footer_line(NANOWELLS, used, total) + " · a cell is the mean over its wells of each well's median"
    return NanowellGrid(arr, err, metric.label if metric else key, vmin, vmax, footer)
