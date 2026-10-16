"""The per-cell scatter over a selection larger than it can draw (tracker F3b).

:func:`~review.features.scatter.collect.collect_scatter_data` collects every cell
of every selected well into Python lists — values, timepoints, and a metadata
tuple per cell for click-to-view. Over most of a plate of 1.1M-cell wells that is
tens of millions of each, and a figure cannot usefully draw them anyway. When a
selection's rows exceed the tab's cap (:data:`SCATTER_MAX_POINTS`), the collector
hands over to :func:`collect_scatter_pooled`, which:

* reads each well narrowly (``review/data/narrow_rows.py``) and filters it with
  the same :func:`~review.features.scatter.collect.well_cloud` the whole-selection
  path uses, so the cells it samples are the cells a small selection draws;
* pools them (``review/data/cell_pool.py``) rather than keeping them;
* draws a proportional sample of :data:`SCATTER_MAX_POINTS` cells per group, each
  keeping its well, file, nucleus and row so a click still opens the right cell;
* and gives each group a :class:`PooledPopulation`, through which the gate
  percentages count **every** cell — a percentage taken from the sample would be
  a different number from the one a smaller selection prints, with nothing to
  say so.

Each group also carries ``n_total`` and ``n_drawn``, and
``scatter._annotations.sampled_note`` turns them into the sentence the figure carries. The exports never come here:
they pass no ``max_points`` and keep every cell.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from domain.tp_match import tp_close_mask
from review.data.cell_pool import CellPool
from review.data.data_loading import get_rows
from review.data.narrow_rows import narrow_rows, value_inputs
from review.features.scatter._cells import well_cloud

#: The most cells the tab draws. Above it, a stated sample is drawn. Matplotlib
#: draws 100,000 points in well under a second, and a density view of a sample
#: this size has the shape of the whole cloud.
SCATTER_MAX_POINTS = 100_000


class PooledPopulation:
    """A group's cells before the tab's gate filter, held in a cell pool.

    What :func:`~review.features.scatter.gating.compute_gate_stats` counts a
    gate against when the group was sampled. Timepoints are matched with the
    app's own tolerant rule, applied to the pool's distinct values.
    """

    def __init__(self, pool, group: str):
        self._pool = pool
        self._group = group

    def total(self) -> int:
        return self._pool.count(self._group)

    def _tps(self, target: float | None) -> list[float] | None:
        if target is None:
            return None
        values = self._pool.distinct("tp", self._group)
        return values[tp_close_mask(values, float(target))].tolist()

    def pct(self, gate: dict, target_tp: float | None = None):
        """``(pct, n_inside, total)`` as the scatter's ``_pct`` returns it, over every cell."""
        tps = self._tps(target_tp)
        total = self._pool.count_at(self._group, tps)
        if total == 0:
            return None, 0, 0
        inside = self._pool.count_box("x", "y", gate, self._group, tps)
        return 100.0 * inside / total, inside, total


def _narrow_columns(col_x: str, col_y: str, gates, ratios) -> set[str] | None:
    """What :func:`well_cloud` reads beyond the identity and verdict columns, or ``None``."""
    want = {"timepoint_hours", "area_px", "filename", "nucleus_id"}
    for key in (col_x, col_y, *(g.get(k) for g in gates or [] for k in ("x_col", "y_col"))):
        cols = value_inputs(key or "", ratios)
        if cols is None:
            return None
        want |= cols
    return want


def close_previous_pools(ctx) -> None:
    """Release the pool the last pooled collection kept for its gate percentages.

    Called by every tab collection, pooled or not, so a pool does not outlive
    the figure that read it; a pool dropped any other way (a dataset switch, the
    app closing) releases itself through ``CellPool``'s finalizer.
    """
    for pool in ctx.runtime.scatter.cell_pools or ():
        pool.close()
    ctx.runtime.scatter.cell_pools = []


def collect_scatter_pooled(ctx, plan, *, col_x: str, col_y: str, target_tps, fluor_gate_x: float,
                           fluor_gate_y: float, gates, ratios, max_points: int) -> dict[str, dict[str, Any]]:
    """``collect_scatter_data``'s result for *plan*, drawn from a stated sample. See the module docstring."""
    close_previous_pools(ctx)
    want = _narrow_columns(col_x, col_y, gates, ratios)
    drawn = CellPool(["x", "y"])
    population = CellPool(["x", "y"]) if gates else drawn
    # Wells that reached the figure, per group, in their order — the
    # whole-selection path gives each one a breakdown entry even when the gate
    # filter left it nothing to draw.
    seen: dict[str, list[str]] = {}
    try:
        for group in plan:
            for well in group.wells:
                df = narrow_rows(ctx, well, want) if want is not None else get_rows(ctx, well)
                cloud = well_cloud(df, col_x=col_x, col_y=col_y, target_tps=target_tps,
                                   fluor_gate_x=fluor_gate_x, fluor_gate_y=fluor_gate_y,
                                   gates=gates, ratios=ratios)
                if cloud is None:
                    continue
                seen.setdefault(group.name, []).append(well)
                df, x, y, tp, mask, ungated, gated = cloud
                rows = df.index.to_numpy()
                drawn.add(group.name, well, rows[mask], tp[mask], {"x": x[mask], "y": y[mask]})
                if gated:
                    population.add(group.name, well, rows[ungated], tp[ungated],
                                   {"x": x[ungated], "y": y[ungated]})
        sample = drawn.sample(max_points)
        identity = _identity_for(ctx, sample, want)
    except BaseException:
        population.close()
        drawn.close()
        raise
    if population is not drawn:
        drawn.close()
    ctx.runtime.scatter.cell_pools = [population]

    out: dict[str, dict[str, Any]] = {}
    for group in plan:
        if population.count(group.name) == 0:
            continue
        got = sample.get(group.name)
        metadata = [] if got is None else [
            (str(w), *identity.get((str(w), int(r)), ("", "")), int(r)) for w, r in zip(got.well, got.row)]
        wells: dict[str, dict] = {w: {"x": [], "y": [], "tp": [], "metadata": []} for w in seen.get(group.name, ())}
        if got is not None:
            for i, w in enumerate(got.well):
                entry = wells.setdefault(str(w), {"x": [], "y": [], "tp": [], "metadata": []})
                entry["x"].append(float(got.values["x"][i]))
                entry["y"].append(float(got.values["y"][i]))
                entry["tp"].append(float(got.tp[i]))
                entry["metadata"].append(metadata[i])
        out[group.name] = {
            "x": [] if got is None else got.values["x"].tolist(),
            "y": [] if got is None else got.values["y"].tolist(),
            "tp": [] if got is None else got.tp.tolist(),
            "color": group.color,
            "metadata": metadata,
            "wells": wells,
            "label": group.label,
            "n_total": 0 if got is None else int(got.total),
            "n_drawn": 0 if got is None else len(got),
            "population": PooledPopulation(population, group.name),
        }
    return out


def _identity_for(ctx, sample, want) -> dict[tuple[str, int], tuple[str, str]]:
    """``(filename, nucleus_id)`` for each sampled cell, read back for the wells that have one."""
    by_well: dict[str, set[int]] = {}
    for got in sample.values():
        for w, r in zip(got.well, got.row):
            by_well.setdefault(str(w), set()).add(int(r))
    out: dict[tuple[str, int], tuple[str, str]] = {}
    for well, rows in by_well.items():
        df = narrow_rows(ctx, well, {"filename", "nucleus_id"}) if want is not None else get_rows(ctx, well)
        idx = sorted(rows)
        sel = df.loc[idx]
        names = sel["filename"].astype(str).to_numpy() if "filename" in sel.columns else np.array([""] * len(sel))
        nids = sel["nucleus_id"].astype(str).to_numpy() if "nucleus_id" in sel.columns else np.array([""] * len(sel))
        for r, f, n in zip(idx, names, nids):
            out[(well, r)] = (f, n)
    return out
