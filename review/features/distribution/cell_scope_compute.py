"""What the Distribution tab is drawing, as a :class:`CellScope`.

The per-cell values this tab plots come from
``data_loading._all_fluor_values_filtered``, which is row-wise throughout: the
inclusion chokepoint with the gate layer on, a finite ``area_px``, the active
per-channel cutoffs, one timepoint, and a finite resolved value. The scope
mirrors that, except that the area check and the cutoffs become the
``plot_in_population`` marker rather than a filter, so the cells the tab
excluded are still in the file and visibly excluded.

It does **not** resolve a missing numeric timepoint through the string column:
this tab's filter is a plain ``tp_close_mask`` on ``timepoint_hours``
(``data_loading.py:862``), and exporting a row the plot never drew with the
marker set would misdescribe the figure rather than add to it.
"""

from __future__ import annotations

import math

from review.features.export.cell_scope_compute import CellScope, scope_from_ctx


def cell_scope(ctx) -> CellScope:
    tp_var = ctx.widgets.get_or_none("_distribution_tp_var")
    tp_str = tp_var.currentText() if tp_var is not None else ""
    try:
        tp_h = float(tp_str) if tp_str not in ("", "—", None) else float("nan")
    except (TypeError, ValueError):
        tp_h = float("nan")
    timepoints = (tp_h,) if math.isfinite(tp_h) else ()

    return scope_from_ctx(
        ctx,
        plot="distribution",
        timepoints=timepoints,
        include_gate=True,
        tp_fallback=False,
        required_columns=("area_px",),
        notes=(("drawn statistic",
                "the distribution of the value column over plot_in_population=1"),),
    )
