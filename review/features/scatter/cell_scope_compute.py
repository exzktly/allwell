"""What the Scatter tabs are drawing, as :class:`CellScope`\\ s.

Two things here are unlike every other tab, and both are deliberate upstream.

**The gate layer is off.** The per-cell scatter is where the gates are *drawn*,
so it shows the whole cloud with the boxes over it rather than filtering itself
by a column another tab's worker wrote (``scatter/collect.py:210-216``). The
export inherits that: ``include_gate=False``, and gate membership becomes the
``plot_in_gate`` marker instead. Keeping the ungated cloud in the file is not a
nicety — a gate's percentage is denominated by the population it was drawn over,
which is the property ``tests/test_plot_export_parity.py`` exists to hold, so a
file filtered down to the gate could not reproduce the percentage on the figure.

**Count and shape columns bypass the intensity cutoff.** An smFISH spot tally or
a morphology value is not a fluorescence intensity, and an intensity floor of a
few hundred would empty the population; ``column_gating_exempt`` owns that rule
(``scatter/collect.py:199``) and the axis markers below follow it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from review.data.data_loading import resolve_value_series
from review.data.metric_labels import SYNTHETIC_METRIC_KEYS
from review.domain.metric_registry import column_gating_exempt
from review.features.export.cell_scope_compute import CellScope, scope_from_ctx
from review.features.scatter.collect import gates_for_axes
from review.features.scatter.controller import (
    col_for_scatter_axis, scatter_axis_metric_key,
)
from review.features.scatter.gating import compute_gate_inclusion


def _axis_marker(col: str, gate: float):
    """Rows passing one axis's own floor — or all of them, if it is exempt."""
    exempt = column_gating_exempt(col)

    def _marker(df, scope):
        if exempt:
            return np.ones(len(df), dtype=bool)
        values = resolve_value_series(df, col, scope.ratios).to_numpy()
        with np.errstate(invalid="ignore"):
            return np.asarray(np.isfinite(values) & (values > gate), dtype=bool)
    return _marker


def _gate_marker(gates, ratios):
    def _marker(df, _scope):
        if not gates:
            return np.ones(len(df), dtype=bool)
        return np.asarray(compute_gate_inclusion(df, gates, ratios), dtype=bool)
    return _marker


def _selected_timepoints(ctx) -> tuple[float, ...]:
    out: list[float] = []
    for tp_str, on in (ctx.runtime.scatter.tp_selections or {}).items():
        if not on:
            continue
        try:
            out.append(float(tp_str))
        except (TypeError, ValueError):
            continue
    if not out:
        cb = ctx.widgets.get_or_none("_scatter_tp_cb")
        try:
            text = cb.currentText() if cb is not None else ""
            if text:
                out.append(float(text))
        except (TypeError, ValueError, AttributeError):
            pass
    return tuple(out)


def cell_scope(ctx, *, entry_x: str | None = None, entry_y: str | None = None,
               col_x: str | None = None, col_y: str | None = None,
               timepoints: tuple[float, ...] | None = None) -> CellScope | None:
    """The per-cell Scatter tab's scope.

    The keyword overrides exist for Batch Export, which plots its **own** axes
    and its own ticked timepoints: reading this tab's combos there produced a
    per-cell file describing the plot on screen, filed beside a figure of two
    different columns. *entry_x* / *entry_y* are the channel entries (they pick
    the per-axis fluor floors), *col_x* / *col_y* the resolved columns, and each
    defaults to what this tab's own controls say — so the tab's call is
    unchanged.
    """
    from review.core.channel_state_controller import get_fluor_gate

    if entry_x is None or entry_y is None:
        ch_x_cb = ctx.widgets.get_or_none("_scatter_ch_x_cb")
        ch_y_cb = ctx.widgets.get_or_none("_scatter_ch_y_cb")
        if ch_x_cb is None or ch_y_cb is None:
            return None
        entry_x = entry_x if entry_x is not None else ch_x_cb.currentText()
        entry_y = entry_y if entry_y is not None else ch_y_cb.currentText()
    if not entry_x or not entry_y:
        return None

    if col_x is None:
        col_x = col_for_scatter_axis(ctx, entry_x,
                                     scatter_axis_metric_key(ctx, "x"))
    if col_y is None:
        col_y = col_for_scatter_axis(ctx, entry_y,
                                     scatter_axis_metric_key(ctx, "y"))
    gate_x = get_fluor_gate(ctx, entry_x.split(" ")[0])
    gate_y = get_fluor_gate(ctx, entry_y.split(" ")[0])
    ratios = dict(ctx.state.channel.ratio_index or {})
    gates = gates_for_axes(ctx, col_x, col_y)

    def _finite_axes(df, scope):
        x = resolve_value_series(df, col_x, scope.ratios).to_numpy()
        y = resolve_value_series(df, col_y, scope.ratios).to_numpy()
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy() \
            if "area_px" in df.columns else np.full(len(df), np.nan)
        with np.errstate(invalid="ignore"):
            return np.asarray(np.isfinite(x) & np.isfinite(y) & np.isfinite(area),
                              dtype=bool)

    return scope_from_ctx(
        ctx,
        plot="scatter_cells",
        val_col=col_x,
        timepoints=(_selected_timepoints(ctx) if timepoints is None
                    else tuple(float(t) for t in timepoints)),
        include_gate=False,
        tp_fallback=False,
        required_columns=("timepoint_hours", "area_px"),
        markers=(
            ("plot_x_above_floor", _axis_marker(col_x, gate_x)),
            ("plot_y_above_floor", _axis_marker(col_y, gate_y)),
            ("plot_axes_finite", _finite_axes),
            ("plot_in_gate", _gate_marker(gates, ratios)),
        ),
        drawn_subset="plot_x_above_floor=1 AND plot_y_above_floor=1 AND "
                     "plot_axes_finite=1 (AND plot_in_gate=1 with Apply gates on)",
        notes=(
            ("axes", f"x={col_x}, y={col_y}"),
            ("gate layer",
             "Gate_Pass is not applied — this tab draws the ungated cloud, which "
             "is also the denominator its gate percentages are computed against"),
        ),
    )


def _agg_timepoints(ctx) -> tuple[float, ...]:
    out: list[float] = []
    for tp_str, on in (ctx.runtime.scatter.agg_tp_selections or {}).items():
        if not on:
            continue
        try:
            out.append(float(tp_str))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def agg_cell_scope(ctx, *, entry_x: str | None = None,
                   metric_x: str | None = None,
                   timepoints: tuple[float, ...] | None = None) -> CellScope | None:
    """The aggregate Scatter tab's scope: the cells behind each well's point.

    Deliberately **not** ``replace(cell_scope(ctx), …)``. The two Scatter pages
    are separate tabs with separate controls — ``_scatter_agg_ch_x_cb``,
    ``_scatter_agg_stat_x_cb`` and ``ctx.runtime.scatter.agg_tp_selections``
    against the per-cell page's own trio — so deriving one scope from the other
    exports the wrong channel at the wrong timepoints under a scope block that
    names a third thing. Right schema, right-looking counts, no error.

    Three further differences from the per-cell page, all following from this
    tab aggregating rather than drawing cells:

    * the gate layer is **on**, because the aggregation filters through the
      inclusion chokepoint like every other summary view — the per-cell page's
      exemption exists only because it is where the gates are drawn;
    * string timepoints resolve, because the path here is
      ``collect_scatter_agg_data`` → ``group_replicate_stats`` →
      ``aggregate_with_threshold_df``, which falls back to the ordinal map;
    * there are no axis-floor markers, because a point is a statistic over a
      well rather than a cell at an (x, y).

    The keyword overrides exist for Batch Export's aggregate mode, which
    exports its own channel, its own statistic and its own ticked timepoints;
    reading this tab's controls there wrote a file describing the tab. Each
    defaults to what this tab says, so the tab's call is unchanged.
    """
    if entry_x is None:
        ch_x_cb = ctx.widgets.get_or_none("_scatter_agg_ch_x_cb")
        if ch_x_cb is None:
            return None
        entry_x = ch_x_cb.currentText()
    if not entry_x:
        return None

    # ``fraction_above_threshold`` / ``events_above_threshold`` are the agg
    # tab's synthetic metric keys: the aggregation treats them as a Fraction-On
    # or an events-above count over the channel's mean-intensity column, so
    # that is the column the exported cells are measured on.
    if metric_x is None:
        metric_x = scatter_axis_metric_key(ctx, "x", agg=True)
    if metric_x in SYNTHETIC_METRIC_KEYS:
        metric_x = "mean_intensity"
    col_x = col_for_scatter_axis(ctx, entry_x, metric_x)

    return scope_from_ctx(
        ctx,
        plot="scatter_aggregate",
        val_col=col_x,
        timepoints=(_agg_timepoints(ctx) if timepoints is None
                    else tuple(float(t) for t in timepoints)),
        include_gate=True,
        tp_fallback=True,
        required_columns=("area_px",),
        notes=(
            ("x axis", f"aggregated from {col_x}"),
            ("drawn statistic",
             "each point is one well's (or replicate set's) aggregate of these "
             "cells — group by well_label to reproduce it"),
            ("replicate sets",
             "a set's point is the mean of its wells' values with a cross-well "
             "SD (ddof=1)"),
        ),
    )
