"""What a plot's points are: cells, or nanowells — the contract between the plot tools and whatever supplies them.

Review had two tables of observations and only one of them was shared. Every plot tool read the per-cell rows; the
per-nanowell rows (count, confluence, brightness, fluorescence, fold changes, growth rate — the Nanowells page's
``series_compute.site_metrics``) were computed inside the Nanowells page and reachable nowhere else, so no plot
could, for instance, set a nanowell's cell count against its fluorescence, and every cell plot had to be rebuilt
on the page to show nanowells at all.

This module names the two kinds of observation and the shape a table of them has, so a plot tool can ask for
nanowell rows without knowing who computes them. The Nanowells feature supplies them through the registry service
:data:`NANOWELL_SERVICE`; a build without that feature has no supplier, and a plot tool then offers cells only
(``review/data/observations``).

**Words.** The control is labelled *Observations*, never "unit": the app already uses "replicate unit" for what a
member of a plot is, and the Nanowells page's statistics have a *nanowells | wells* sample choice. Wells stay the
replicates whatever the observations are; a nanowell is a point, never a replicate between conditions.

Qt-free and pure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

CELLS = "cells"
NANOWELLS = "nanowells"
#: What the toggle shows for each.
OBSERVATION_LABELS = {CELLS: "Cells", NANOWELLS: "Nanowells"}
#: The registry service the Nanowells feature answers with an :class:`ObservationTable`.
NANOWELL_SERVICE = "observations.nanowell"
#: The registry service the same feature answers with those observations' metrics alone (no rows read), for a
#: picker that lists them while a plate opens.
NANOWELL_METRICS_SERVICE = "observations.nanowell_metrics"

#: A per-cell column holding the cell's own nanowell's metric: ``nw::<metric key>``. A nanowell metric is not a
#: fluorescence intensity, so no ON cutoff or fluorescence gate applies to it (``metric_registry``).
NANOWELL_CELL_PREFIX = "nw::"


def is_nanowell_cell_column(column) -> bool:
    return isinstance(column, str) and column.startswith(NANOWELL_CELL_PREFIX)


#: Metrics that are 0/1 flags, whose well summary is a share (the mean), never a median.
SHARE_METRICS = frozenset({"occupied"})
#: The columns every nanowell observation row carries besides its metrics.
NANOWELL_ID_COLUMNS = ("well", "timepoint", "time_h", "nw_q", "nw_r", "state")


@dataclass(frozen=True)
class ObservationMetric:
    #: The frame column holding the value.
    key: str
    #: What a figure calls it.
    label: str
    #: ``"value"`` (measured at a timepoint), ``"fc"`` (a timepoint over the baseline), ``"site"`` (one number per
    #: nanowell over time) or ``"base"`` (one number per nanowell, taken at the baseline).
    kind: str = "value"
    #: The fluorescence channel the metric is of, ``""`` for one of none.
    channel: str = ""
    #: How a well's observations reduce to the well's one value: ``"mean"`` or ``"median"``; ``""`` leaves it to
    #: :func:`well_statistic`'s default (the mean for a 0/1 metric, the median otherwise).
    well_statistic: str = ""


def well_statistic(table: ObservationTable | None, key: str) -> str:
    """``"mean"`` or ``"median"``: how *key*'s observations in a well become the well's one value — the metric's own
    choice when it states one (the Nanowells page's size classes, whose well value is their mean), else the mean for
    the 0/1 metrics (:data:`SHARE_METRICS`) and the median for the rest."""
    spec = table.metric(key) if table is not None else None
    if spec is not None and spec.well_statistic in ("mean", "median"):
        return spec.well_statistic
    return "mean" if key in SHARE_METRICS else "median"


@dataclass
class ObservationTable:
    """Rows of one kind of observation, the metrics that may be plotted from them, and why a value is missing."""

    observations: str
    frame: pd.DataFrame
    metrics: tuple[ObservationMetric, ...]
    #: ``metric key -> Series`` (aligned to ``frame``) of why the value is undefined, ``""`` where defined.
    why_missing: Callable[[str], pd.Series] | None = None
    #: Sentences every figure drawn from this table carries (a rim-focus rule, an identity verdict).
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Whether a plot keeps only the nanowells with its metrics defined at every timepoint of their well (the
    #: Nanowells page's *Fixed cohort*, carried by a table asked for with the page's scope). The plot applies it,
    #: because which values must be complete depends on which metrics it draws (:func:`cohort_complete`).
    fixed_cohort: bool = False
    #: How a fold change in this table is measured from — the Nanowells page's Baseline in words (``earliest``,
    #: ``first measurable``, a timepoint's name); ``""`` where the supplier does not say.
    baseline: str = ""
    #: The name of the Nanowells page's Subset the rows were narrowed to (``Subset.label()``), ``""`` for none — so a
    #: plot can say what its percentages are of.
    subset: str = ""

    def metric(self, key: str) -> ObservationMetric | None:
        return next((m for m in self.metrics if m.key == key), None)

    def values(self, key: str) -> pd.Series:
        """The metric's values as floats (undefined → NaN)."""
        if key not in self.frame.columns:
            return pd.Series(float("nan"), index=self.frame.index)
        return pd.to_numeric(self.frame[key], errors="coerce")

    def exclusions(self, *keys: str) -> dict[str, int]:
        """``reason -> rows`` for the rows where any of *keys* is undefined, the first key's reason first."""
        out: dict[str, int] = {}
        if self.frame.empty:
            return out
        missing = pd.Series(False, index=self.frame.index)
        reason = pd.Series("", index=self.frame.index, dtype=object)
        for key in keys:
            undefined = self.values(key).isna() & ~missing
            if not undefined.any():
                continue
            why = self.why_missing(key) if self.why_missing is not None else pd.Series("", index=self.frame.index)
            reason[undefined] = why[undefined].replace("", "no value").fillna("no value")
            missing |= undefined
        for text, count in reason[missing].value_counts().items():
            out[str(text)] = int(count)
        return out


#: The reason a row is left out of a plot by the fixed cohort, as the Nanowells page counts it.
COHORT_REASON = "outside the fixed cohort"
#: Metric kinds with one value per nanowell rather than one per timepoint; the fixed cohort does not judge them.
PER_NANOWELL_KINDS = frozenset({"site", "base"})


def cohort_complete(frame: pd.DataFrame, defined: pd.Series) -> pd.Series:
    """Per row, whether its nanowell (``well``, ``nw_q``, ``nw_r``) has *defined* true at every one of its rows —
    every timepoint of its well. The Nanowells page's fixed-cohort rule (``distribution_compute.fixed_cohort``),
    asked of several metrics at once by passing their joint definedness."""
    if frame.empty:
        return pd.Series(True, index=frame.index)
    key = [frame["well"], frame["nw_q"], frame["nw_r"]]
    return defined.groupby(key).transform("all").astype(bool)


def footer_line(observations: str, used: int, total: int, exclusions: dict[str, int] | None = None) -> str:
    """The first footer line of any figure drawn from observations.

    It begins ``"Observations: cells"`` or ``"Observations: nanowells"`` and then counts what was used and what was
    left out with why — so a figure exported from the plot says what its points are and what it lost.
    """
    head = f"Observations: {observations}"
    if observations == CELLS:
        return head
    parts = [head, f"{used} of {total} used"]
    if exclusions:
        reasons = ", ".join(f"{why} {n}" for why, n in sorted(exclusions.items(), key=lambda kv: (-kv[1], kv[0])))
        parts.append(f"excluded: {reasons}")
    return " · ".join(parts)


def pooled_cells_line(occupied_by_well: dict[str, int], *, nanowell_views: bool) -> str:
    """The footer of a cell plot on a nanowell plate: the cells are pooled from the nanowells that hold any.

    *occupied_by_well* counts distinct nanowell addresses among each well's cell rows — the nanowells holding a
    cell, not the nanowells the well has (an empty nanowell has no cell row), so the sentence says "occupied".
    *nanowell_views* is whether this build has the Nanowells feature; without it the sentence says the views are
    not in this build rather than leaving the user to look for them.
    """
    counts = sorted(set(occupied_by_well.values()))
    if not counts:
        n_text = "no"
    elif len(counts) == 1:
        n_text = str(counts[0])
    else:
        n_text = f"{counts[0]}–{counts[-1]}"
    line = f"Observations: cells · pooled from {n_text} occupied nanowells per well"
    if not nanowell_views:
        line += " · nanowell views are not in this build"
    return line


def well_medians(table: ObservationTable, key: str, wells, timepoints_h=None, *, how: str | None = None) -> pd.DataFrame:
    """Each well's median of metric *key* over its nanowells, per timepoint.

    Columns ``well``, ``timepoint``, ``timepoint_hours``, ``median``, ``n`` (nanowells with a value) and
    ``total`` (nanowell rows). A well-timepoint with no defined value has ``median`` NaN and ``n`` 0 — not
    computed is not zero (CLAUDE.md §7). *timepoints_h* restricts to those hours (``timepoint_hours``).

    *how* ``"mean"`` takes the mean instead, and is the only right reduction of a 0/1 metric such as ``occupied``:
    its mean is the share of nanowells, its median merely says whether more than half qualify. The column keeps the
    name ``median`` so every caller reads one shape. Left unset, *how* is the metric's own (:func:`well_statistic`).
    """
    how = how or well_statistic(table, key)
    from domain.tp_match import tp_close

    frame = table.frame
    cols = ["well", "timepoint", "timepoint_hours", "median", "n", "total"]
    if frame.empty or key not in frame.columns:
        return pd.DataFrame(columns=cols)
    scope = frame["well"].isin(list(wells))
    if timepoints_h is not None:
        wanted = [float(t) for t in timepoints_h]
        hours = pd.to_numeric(frame["timepoint_hours"], errors="coerce")
        scope &= hours.map(lambda h: h == h and any(tp_close(h, t) for t in wanted))
    rows = frame[scope].assign(_v=table.values(key)[scope])
    out = (rows.groupby(["well", "timepoint", "timepoint_hours"], sort=True)["_v"]
           .agg(median=how, n="count", total="size").reset_index())
    return out[cols]


@dataclass(frozen=True)
class GroupPoint:
    """One group at one timepoint: the mean of its wells' medians, the spread over wells, how many wells."""

    label: str
    timepoint_hours: float
    mean: float
    sem: float          # NaN with fewer than two wells: one well has no spread over wells
    n_wells: int
    well_values: tuple[float, ...]


def group_points(medians: pd.DataFrame, groups) -> list[GroupPoint]:
    """Per group (``label``, ``wells``) and timepoint, the wells are the replicates: mean ± SEM over well medians.

    Nanowells are never pooled across wells into one sample — that would treat nanowells as replicates.
    """
    import math

    out: list[GroupPoint] = []
    if medians.empty:
        return out
    for label, wells in groups:
        mine = medians[medians["well"].isin(list(wells)) & medians["median"].notna()]
        for hours, sub in mine.groupby("timepoint_hours", sort=True):
            vals = tuple(float(v) for v in sub["median"])
            n = len(vals)
            mean = sum(vals) / n
            sem = (math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) / math.sqrt(n)) if n >= 2 else float("nan")
            out.append(GroupPoint(label, float(hours), mean, sem, n, vals))
    return out


def scoped_counts(table: ObservationTable, key: str, wells, timepoints_h=None) -> tuple[int, int, dict[str, int]]:
    """``(used, total, excluded by reason)`` of metric *key* over the rows a figure draws — *wells* at
    *timepoints_h* — so a footer's numbers add up: ``used + sum(excluded) == total``."""
    from domain.tp_match import tp_close

    frame = table.frame
    if frame.empty:
        return 0, 0, {}
    scope = frame["well"].isin(list(wells))
    if timepoints_h is not None:
        wanted = [float(t) for t in timepoints_h]
        hours = pd.to_numeric(frame["timepoint_hours"], errors="coerce")
        scope &= hours.map(lambda h: h == h and any(tp_close(h, t) for t in wanted))
    values = table.values(key)[scope]
    defined = values.notna()
    excluded: dict[str, int] = {}
    if (~defined).any():
        why = (table.why_missing(key)[scope] if table.why_missing is not None
               else pd.Series("", index=values.index))
        for text, n in why[~defined].replace("", "no value").fillna("no value").value_counts().items():
            excluded[str(text)] = int(n)
    return int(defined.sum()), int(scope.sum()), excluded


def wells_without_value(medians: pd.DataFrame) -> list[str]:
    """``"<well> (t=<h>h)"`` for each well-timepoint whose median is undefined — a well that fell out of its
    member's mean and spread, which a figure must name rather than drop silently."""
    if medians.empty:
        return []
    gone = medians[medians["median"].isna()]
    return [f"{w} (t={h:g}h)" for w, h in zip(gone["well"], gone["timepoint_hours"])]
