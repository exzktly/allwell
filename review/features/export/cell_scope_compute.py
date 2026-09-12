"""The rows behind a plot, and the columns that say what the plot did with them.

Every Review plot is computed from per-cell rows, and until now none of them
could hand those rows back: the per-tab data exports write the plotted summary
(one row per bar, per line point, per heat-map cell), and the three that were
per-cell wrote one or two columns of it. This module is the shared half of the
answer — each plot feature contributes a :class:`CellScope` from its own
``collect.py`` and this turns it into rows.

**The rule, and why it is not "the rows behind a drawn point".** Defining the
export as the row set a drawn point was computed from is undefined on several
tabs: a Heat Map cell under the mean metric is a mean of *per-well means*, not a
function of any row set, and a Bar tab with violin on draws two populations at
once. So the export is instead:

    every row of every well the plot is drawing, at the timepoints it is
    drawing, that passes that tab's inclusion layers and has a finite value —
    with every narrowing the plot applies beyond that recorded as a **marker
    column**, never as a filter.

Nothing is dropped, so nothing has to be reconstructed, and any statistic the
tab draws can be recomputed from the file — including a mean of per-well means,
given ``well_label``. The file can therefore hold rows the figure did not draw;
it says so per row, which is the shortfall being visible *in the artifact*
rather than in a log line.

The scope block written above the header names which marker the figure drew,
so a file is never ambiguous about which subset it was.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from review.data.data_loading import (
    df_included_mask,
    resolve_value_series,
    resolved_timepoints,
)
from review.domain.gating_state import active_fluor_gates
from domain.tp_match import tp_close_mask

#: Verdict columns the app computes and the CSV on disk never carries. Absent is
#: not unknown: ``data/cache_projectors.project_verdict_column`` *drops* one of
#: these when its verdict raises, and every consumer then reads absent as
#: all-pass (``df_included_mask``), so a well can legally lack ``QC_Pass`` while
#: its neighbours have it. Writing a blank there would say "unknown" where the
#: app means "passed", so an absent layer is written as 1 and named in the scope
#: block instead.
VERDICT_COLUMNS = ("QC_Pass", "Crop_Pass", "Gate_Pass")

#: The two markers every scope carries. ``plot_in_population`` is the narrower
#: set the tab's own statistic is computed over (finite area, per-channel
#: cutoffs); ``plot_above_threshold`` is the "on" half of that population.
MARKER_IN_POPULATION = "plot_in_population"
MARKER_ABOVE_THRESHOLD = "plot_above_threshold"

#: Markers are nullable ``Int8`` rather than plain ints. A plain int column
#: turns ``float64`` the moment one NaN lands in it and then writes ``1.0`` /
#: ``0.0``; ``Int8`` writes ``1``, ``0`` and an empty field for ``pd.NA``, which
#: is the three-way present / absent / not-computed distinction a reader needs.
MARKER_DTYPE = "Int8"

MarkerFn = Callable[[pd.DataFrame, "CellScope"], Any]


@dataclass(frozen=True)
class CellScope:
    """What one plot is about, in the terms this module can turn into rows.

    ``threshold`` and ``ratios`` are not optional extras. ``val_col`` may be a
    ``ratio:<name>`` key, which :func:`resolve_value_series` can only resolve
    given the ratio index, and ``plot_above_threshold`` is defined by the
    threshold — a scope that cannot reproduce the value column is not a
    description of the row set.

    ``on_col`` carries the alternative definition of "on": when the scatter-gate
    thresholding mode is active the tab's own aggregation partitions on a
    boolean column (``Gate_On``) instead of ``value > threshold``, and the
    marker has to be computed the way the tab computes it or the file
    contradicts the figure.

    ``tp_fallback`` says whether this tab resolves a missing numeric timepoint
    through the string column. Bar and Line's aggregation does; Distribution and
    Scatter do not. Using the fallback where the tab does not would export rows
    the plot never drew *with the marker set to 1*, which is worse than not
    exporting them.
    """

    plot: str
    val_col: str
    ratios: dict
    threshold: float
    fluor_gates: dict
    timepoints: tuple[float, ...] = ()
    include_gate: bool = True
    on_col: str | None = None
    tp_fallback: bool = False
    tp_col: str = "timepoint_hours"
    #: ``(group_name, well_label)`` in draw order, resolved when the user clicks.
    wells: tuple[tuple[str, str], ...] = ()
    #: Extra per-tab marker columns, appended after the two standard ones.
    markers: tuple[tuple[str, MarkerFn], ...] = ()
    #: Which marker value the figure actually drew, for the scope block.
    drawn_subset: str = MARKER_IN_POPULATION
    #: Free-form ``(key, value)`` lines added to the scope block — the place a
    #: tab records something the columns cannot say, such as a plotted value
    #: being a mean of per-well means.
    notes: tuple[tuple[str, str], ...] = ()
    #: Columns the tab needs present; a well lacking one contributes no rows.
    required_columns: tuple[str, ...] = ()
    extras: dict = field(default_factory=dict)


def plotted_wells(ctx, *, fallback_to_all: bool = False) -> tuple[tuple[str, str], ...]:
    """``(group_name, well_label)`` for the wells the plot is drawing.

    Goes through ``data_loading.iter_plot_group_wells``, which shares its
    selection with the pooled generator the renderers use, so the export cannot
    drift from the figure about which wells are on screen — the failure that
    would otherwise be a plausible file of the wrong wells.
    """
    from review.data.data_loading import iter_plot_group_wells
    return tuple((group, well)
                 for group, well, _df in iter_plot_group_wells(ctx, fallback_to_all))


def scope_from_ctx(ctx, *, plot: str, val_col: str | None = None,
                   threshold: float | None = None, **kwargs) -> CellScope:
    """A :class:`CellScope` with the shared ctx-derived fields already filled.

    The value column, ratio index, per-channel cutoffs and ON threshold are the
    same four reads on every tab, and getting any of them from somewhere else is
    how an export stops describing the plot beside it. A tab overrides only what
    it genuinely does differently.
    """
    from review.core.channel_state_controller import (
        get_all_fluor_gates, on_cutoff_for_column,
    )
    col = val_col or ctx.state.channel.active_val_col
    if threshold is None:
        try:
            threshold = float(on_cutoff_for_column(ctx, col))
        except (TypeError, ValueError):
            threshold = float("-inf")
    kwargs.setdefault("wells", plotted_wells(ctx))
    return CellScope(
        plot=plot,
        val_col=col,
        ratios=dict(ctx.state.channel.ratio_index or {}),
        threshold=float(threshold),
        fluor_gates=dict(get_all_fluor_gates(ctx)),
        **kwargs,
    )


def timepoint_values(df: pd.DataFrame, scope: CellScope,
                     rows_mask: np.ndarray | None = None) -> np.ndarray:
    """Each row's timepoint in hours, by this scope's own resolution rule."""
    if scope.tp_fallback:
        return resolved_timepoints(df, scope.tp_col, rows_mask=rows_mask)
    if scope.tp_col not in df.columns:
        return np.full(len(df), np.nan)
    return pd.to_numeric(df[scope.tp_col], errors="coerce").to_numpy().astype(float)


def population_mask(df: pd.DataFrame, scope: CellScope) -> np.ndarray:
    """The rows this scope exports: inclusion, timepoint, and a finite value.

    Deliberately *not* the tab's full filter — the per-channel cutoffs and the
    area check are marker columns, not filters, so that the denominator a
    fraction was computed against survives into the file.
    """
    if df is None or df.empty:
        return np.zeros(0, dtype=bool)
    for col in scope.required_columns:
        if col not in df.columns:
            return np.zeros(len(df), dtype=bool)

    mask = df_included_mask(df, include_gate=scope.include_gate).to_numpy(copy=True)

    tp = timepoint_values(df, scope, rows_mask=mask)
    if scope.timepoints:
        with np.errstate(invalid="ignore"):
            hit = np.zeros(len(df), dtype=bool)
            for t in scope.timepoints:
                hit |= tp_close_mask(tp, float(t))
            mask &= np.isfinite(tp) & hit
    elif scope.tp_fallback:
        # The aggregation drops a row whose timepoint will not resolve even with
        # no timepoint filter set, because it cannot be placed on the x axis.
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(tp)

    val = _values(df, scope)
    with np.errstate(invalid="ignore"):
        mask &= np.isfinite(val)
    return mask


def _values(df: pd.DataFrame, scope: CellScope) -> np.ndarray:
    try:
        return resolve_value_series(df, scope.val_col, scope.ratios).to_numpy()
    except (KeyError, ValueError, TypeError):
        return np.full(len(df), np.nan)


def in_population(df: pd.DataFrame, scope: CellScope) -> np.ndarray:
    """The narrower set the tab's statistic is computed over.

    Finite ``area_px`` plus every active per-channel intensity cutoff — the
    filter ``aggregate_with_threshold_df`` and ``_all_fluor_values_filtered``
    both apply before computing anything.
    """
    n = len(df)
    if "area_px" not in df.columns:
        return np.zeros(n, dtype=bool)
    keep = np.ones(n, dtype=bool)
    area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
    with np.errstate(invalid="ignore"):
        keep &= np.isfinite(area)
    for channel, gate in active_fluor_gates(scope.fluor_gates or {}).items():
        col = f"{channel}_mean_intensity"
        if col not in df.columns:
            # The aggregation drops the whole well here and the FOV-pooled
            # collector keeps it; either way no row of this well is inside the
            # statistic's population, which is what the marker records.
            return np.zeros(n, dtype=bool)
        v = pd.to_numeric(df[col], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            keep &= np.isfinite(v) & (v > gate)
    return keep


def above_threshold(df: pd.DataFrame, scope: CellScope) -> np.ndarray:
    """The "on" half of the population, by whichever rule the tab uses."""
    if scope.on_col and scope.on_col in df.columns:
        return pd.to_numeric(df[scope.on_col], errors="coerce").fillna(0).to_numpy() > 0.5
    val = _values(df, scope)
    with np.errstate(invalid="ignore"):
        return np.asarray(val > scope.threshold, dtype=bool)


def _marker_series(values, index) -> pd.Series:
    arr = np.asarray(values)
    if arr.dtype == bool:
        return pd.Series(pd.array(arr.astype("int8"), dtype=MARKER_DTYPE), index=index)
    out = pd.array([pd.NA if v is None or (isinstance(v, float) and math.isnan(v))
                    else int(v) for v in arr], dtype=MARKER_DTYPE)
    return pd.Series(out, index=index)


def build_chunk(df: pd.DataFrame, scope: CellScope, *,
                group: str, well_label: str, well_name: str) -> pd.DataFrame:
    """One well's exported rows: identity, the frame's own columns, markers.

    The slice is a boolean ``.loc``, which materialises a copy — a positional
    ``.iloc`` slice would be a *view* onto the cached frame, which another
    thread rewrites columns on, so the copy is what makes the hand-off to the
    writer thread safe.
    """
    mask = population_mask(df, scope)
    if not mask.any():
        return df.iloc[:0].copy()
    out = df.loc[mask].copy()

    # Identity columns are built fresh as plain object. Reusing the frame's own
    # ``well`` would inherit its categorical dtype (and its unused categories),
    # and it is a different value anyway: that column is parsed from the image
    # filename and need not equal the token the app keys wells by.
    out.insert(0, "group", pd.Series([group] * len(out), index=out.index, dtype=object))
    out.insert(1, "well_label",
               pd.Series([well_label] * len(out), index=out.index, dtype=object))
    out.insert(2, "well_name",
               pd.Series([well_name] * len(out), index=out.index, dtype=object))

    for col in VERDICT_COLUMNS:
        if col not in out.columns:
            out[col] = _marker_series(np.ones(len(out), dtype=bool), out.index)

    sub = df.loc[mask]
    out[MARKER_IN_POPULATION] = _marker_series(in_population(sub, scope), out.index)
    out[MARKER_ABOVE_THRESHOLD] = _marker_series(above_threshold(sub, scope), out.index)
    for name, fn in scope.markers:
        values = fn(sub, scope)
        # A boolean marker becomes Int8 so it reads back as 0 / 1 / blank; a
        # marker that carries a label instead (the heat-map cell a row feeds)
        # is passed through as it is.
        if np.asarray(values).dtype == bool:
            out[name] = _marker_series(values, out.index)
        else:
            out[name] = pd.Series(list(values), index=out.index, dtype=object)
    return out


def union_header(columns: Sequence[Sequence[str]]) -> list[str]:
    """The column order of the file: first-seen order across every well.

    A column one well lacks is written blank for that well, never ``0`` — a
    measurement that was not made is not a measurement of zero. The verdict
    layers are the documented exception (see :data:`VERDICT_COLUMNS`).
    """
    seen: list[str] = []
    known: set[str] = set()
    for cols in columns:
        for c in cols:
            if c not in known:
                known.add(c)
                seen.append(c)
    return seen


#: The three identity columns every row carries, in file order. ``well_label``
#: is deliberately not ``well``: the frame already has a ``well`` parsed from the
#: image filename, and the two can differ, so both are kept.
IDENTITY_COLUMNS = ("group", "well_label", "well_name")


def header_for(frames_columns: Sequence[Sequence[str]], scope: CellScope) -> list[str]:
    """The file's full column order, in the order :func:`build_chunk` produces.

    Computed up front from every well in scope so a column one well lacks still
    gets a place in the header (and a blank in that well's rows) rather than
    shifting the file's shape halfway down.
    """
    body = union_header(frames_columns)
    out = list(IDENTITY_COLUMNS)
    out += [c for c in body if c not in IDENTITY_COLUMNS]
    out += [c for c in VERDICT_COLUMNS if c not in out]
    out += [MARKER_IN_POPULATION, MARKER_ABOVE_THRESHOLD]
    out += [name for name, _fn in scope.markers if name not in out]
    return out


def missing_verdict_layers(frames_columns: Sequence[Sequence[str]]) -> list[str]:
    """Verdict layers absent from at least one well, for the scope block."""
    out = []
    for col in VERDICT_COLUMNS:
        if any(col not in cols for cols in frames_columns):
            out.append(col)
    return out
