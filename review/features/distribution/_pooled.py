"""The Distribution tab over a selection larger than it draws (tracker F3c).

``redraw_distribution`` pools every selected well of a group into one frame
(``iter_plot_groups``) and filters its values into an array per group; the
renderers then draw a histogram, a KDE, a CDF or a violin from those arrays. Over
most of a plate of 1.1M-cell wells that is tens of millions of values per group,
and the KDE alone evaluates a 200-point grid against every one of them.

When the selection's rows exceed :data:`DIST_MAX_VALUES`,
:func:`collect_distribution` instead reads each well narrowly, filters it with
the same ``_all_fluor_values_filtered`` the whole-selection path applies, and
pools the values (``review/data/cell_pool.py``). Then:

* **every number the figure states comes from every value** — the counts in the
  legend, facet labels and title, the range the bins and KDE grid span, the
  histogram's bars (counted over the whole pool, chunk by chunk) and the violin's
  median line (:class:`ExactStats`);
* **the curves are drawn from a stated sample** — the KDE, the CDF and the violin
  body — of :data:`DIST_MAX_VALUES` values, in proportion per group, and
  :func:`sampled_note` says so on the figure.

A selection whose rows fit under the cap never comes here, so it draws exactly
what it always did. The CSV export does not come here either: it keeps every
value.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from review.data.cell_pool import CellPool, cells_upper_bound
from review.data.data_loading import (
    _all_fluor_values_filtered,
    get_rows,
    plot_group_color,
    plot_group_well_lists,
)
from review.data.narrow_rows import narrow_rows, value_inputs
from review.domain.gating_state import active_fluor_gates

#: The most values the tab draws its curves from. A KDE over 200,000 values on
#: a 200-point grid is 40M kernel evaluations — about a second — and a CDF or a
#: violin body of that many is indistinguishable from the whole at screen size.
DIST_MAX_VALUES = 200_000


@dataclass(frozen=True)
class ExactStats:
    """What a sampled group's figure states, over every one of its values."""

    n: int
    lo: float
    hi: float
    median: float
    #: ``edges -> counts``: the group's histogram over every value.
    hist: Callable[[np.ndarray], np.ndarray]


def _columns(val_col: str, fluor_gates, ratios) -> set[str] | None:
    """What ``_all_fluor_values_filtered`` reads beyond the identity and verdict columns."""
    inputs = value_inputs(val_col, ratios)
    if inputs is None:
        return None
    return inputs | {"area_px", "timepoint_hours"} | {
        f"{channel}_mean_intensity" for channel in active_fluor_gates(fluor_gates or {})}


def collect_distribution(ctx, *, val_col: str, fluor_gates, ratios, tp_filter, display_label) -> tuple | None:
    """``(groups, exact, pool)`` for the tab, or ``None`` when the selection fits under the cap.

    *groups* is ``[(label, colour, values)]`` as ``redraw_distribution`` always
    built it, the values a sample; *exact* a list aligned with it whose entries
    the renderers state instead of what the sample would give. The caller
    closes *pool* once the figure is drawn — :class:`ExactStats` reads it.
    ``None`` sends the caller down its whole-selection path unchanged.
    """
    plan = plot_group_well_lists(ctx, False)
    if cells_upper_bound(ctx, [w for _name, wells, _rset in plan for w in wells]) <= DIST_MAX_VALUES:
        return None
    want = _columns(val_col, fluor_gates, ratios)
    pool = CellPool(["v"])
    for name, wells, _rset in plan:
        for well in wells:
            df = narrow_rows(ctx, well, want) if want is not None else get_rows(ctx, well)
            vals = _all_fluor_values_filtered(df, val_col=val_col, fluor_gates=fluor_gates,
                                              ratios=ratios, tp_filter=tp_filter)
            pool.add(name, well, np.arange(vals.size), np.zeros(vals.size), {"v": vals})
    sample = pool.sample(DIST_MAX_VALUES)
    groups, exact = [], []
    for name, _wells, rset in plan:
        if pool.count(name) == 0:
            continue
        lo, hi = pool.value_range("v", name)
        groups.append((display_label(ctx, name), plot_group_color(ctx, name, rset), sample[name].values["v"]))
        exact.append(ExactStats(
            n=pool.count(name), lo=lo, hi=hi,
            median=float(pool.quantiles("v", [0.5], name)[0]),
            hist=lambda edges, _name=name: pool.histogram("v", edges, _name),
        ))
    return groups, exact, pool


def exact_range(exact) -> tuple[float, float] | None:
    """``(lo, hi)`` over every group's values, or ``None`` when nothing is exact."""
    stats = [e for e in exact or () if e is not None]
    if not stats:
        return None
    return min(e.lo for e in stats), max(e.hi for e in stats)


def sampled_note(groups, exact) -> str:
    """The sentence a sampled figure carries, or ``""`` when every value is drawn."""
    if not exact:
        return ""
    total = sum(e.n for e in exact)
    drawn = sum(int(np.asarray(vals).size) for _label, _color, vals in groups)
    if drawn >= total:
        return ""
    return (f"Curves drawn from a sample of {drawn:,} of {total:,} values; "
            "counts, bins and medians use every value")
