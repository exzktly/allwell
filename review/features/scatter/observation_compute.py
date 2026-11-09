"""Scatter of nanowell observations: any nanowell metric against any other (ARCHITECTURE §6.2l).

With the band's Observations set to Nanowells, a point is one nanowell at one timepoint — or, under
**Points: per well**, one well's median over its nanowells at one timepoint. The rows are the Nanowells feature's
(``review/data/observations``); nothing here computes a metric, so a value on this plot is the value on the page.

A row is drawn only where both metrics are defined; the rest are counted by why (the first axis's reason first),
and the footer says so (``observation.footer_line``). Wells stay the replicates: a per-well point is a well's
median, and a replicate set is drawn as its wells' points in one colour — nanowells are never pooled across
wells into one statistic here.

**Which nanowells.** The table is the Nanowells page's scope (``nanowell_observations(page_scope=True)``): its
excluded wells and its Subset are already applied, and the sentences saying so are the table's notes. The page's
Fixed cohort is applied here, because which values must be complete depends on the two metrics drawn: a nanowell
is kept only when both are defined at every timepoint of its well (a metric with one value per nanowell is not
judged), and the rest are counted in the footer as outside the fixed cohort — as the page counts them. A fold
change is measured from the page's Baseline, and a figure with one on an axis says which.

Qt-free.
"""

from __future__ import annotations

from domain.tp_match import tp_close
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.domain.observation import (COHORT_REASON, NANOWELLS, PER_NANOWELL_KINDS, ObservationTable, well_statistic,
                                       cohort_complete, footer_line)


@dataclass
class NanowellSeries:
    label: str
    color: object
    x: np.ndarray
    y: np.ndarray
    #: ``(well, timepoint, q, r)`` of each point (``q``/``r`` are ``None`` for a per-well median).
    keys: list = field(default_factory=list)


@dataclass
class NanowellScatter:
    series: list[NanowellSeries]
    x_label: str
    y_label: str
    footer: str
    notes: tuple[str, ...] = ()


def _hours(label) -> float:
    from pipeline.nanowell_task import timepoint_hours

    return timepoint_hours(label)


def _in_timepoints(frame: pd.DataFrame, timepoints_h) -> pd.Series:
    """Rows whose timepoint, read the way the cell rows' ``timepoint_hours`` is, is among *timepoints_h*."""
    if timepoints_h is None:
        return pd.Series(True, index=frame.index)
    wanted = [float(t) for t in timepoints_h]
    hours = frame["timepoint"].map(_hours)
    return hours.map(lambda h: any(tp_close(h, t) for t in wanted) if h == h else False)


def nanowell_scatter(table: ObservationTable, x_key: str, y_key: str, groups, *, timepoints_h=None,
                     per_well: bool = False) -> NanowellScatter:
    """The series to draw. *groups* are ``scatter.collect.ScatterGroup`` (label, colour, wells), in legend order."""
    frame = table.frame
    x_metric, y_metric = table.metric(x_key), table.metric(y_key)
    x_label = x_metric.label if x_metric else x_key
    y_label = y_metric.label if y_metric else y_key
    wells = {w for g in groups for w in g.wells}
    outside_cohort = _outside_cohort(table, (x_metric, y_metric), (x_key, y_key))
    scope = frame["well"].isin(wells) & _in_timepoints(frame, timepoints_h) if len(frame) else pd.Series(dtype=bool)
    rows = frame[scope]
    x, y = table.values(x_key)[scope], table.values(y_key)[scope]
    both = x.notna() & y.notna()
    sub = ObservationTable(table.observations, rows, table.metrics, table.why_missing and (
        lambda key: table.why_missing(key)[scope]), table.notes)
    excluded = sub.exclusions(x_key, y_key)
    cut = outside_cohort[scope] & both
    if cut.any():
        excluded[COHORT_REASON] = excluded.get(COHORT_REASON, 0) + int(cut.sum())
        both = both & ~cut
    series = []
    for group in groups:
        mine = rows["well"].isin(group.wells) & both
        if per_well:
            medians = (pd.DataFrame({"well": rows["well"], "timepoint": rows["timepoint"], "x": x, "y": y})[mine]
                       .groupby(["well", "timepoint"], sort=True)
                       .agg({"x": well_statistic(table, x_key), "y": well_statistic(table, y_key)}))
            series.append(NanowellSeries(group.label, group.color, medians["x"].to_numpy(float),
                                         medians["y"].to_numpy(float),
                                         [(w, tp, None, None) for w, tp in medians.index]))
        else:
            picked = rows[mine]
            series.append(NanowellSeries(group.label, group.color, x[mine].to_numpy(float), y[mine].to_numpy(float),
                                         list(zip(picked["well"], picked["timepoint"], picked["nw_q"], picked["nw_r"]))))
    used = int(both.sum())
    footer = footer_line(NANOWELLS, used, int(len(rows)), excluded)
    if per_well:
        footer += " · one point per well and timepoint: the median over its nanowells"
    notes = list(table.notes)
    if table.baseline and any(m is not None and m.kind == "fc" for m in (x_metric, y_metric)):
        notes.append(f"Fold changes are over the Nanowells page's Baseline: {table.baseline}.")
    return NanowellScatter(series, x_label, y_label, footer, tuple(notes))


def _outside_cohort(table: ObservationTable, metrics, keys) -> pd.Series:
    """Per row of *table*, whether the page's Fixed cohort leaves it out of a plot of *keys*: its nanowell lacks one
    of the per-timepoint metrics among them at some timepoint of its well. All ``False`` with the cohort off."""
    frame = table.frame
    judged = [k for k, m in zip(keys, metrics) if m is None or m.kind not in PER_NANOWELL_KINDS]
    if not table.fixed_cohort or not judged or not len(frame):
        return pd.Series(False, index=frame.index)
    defined = pd.Series(True, index=frame.index)
    for key in dict.fromkeys(judged):
        defined &= table.values(key).notna()
    return ~cohort_complete(frame, defined)


def default_axes(table: ObservationTable) -> tuple[str, str]:
    """Two different metrics to open on: the cell count against the first fluorescence metric, else confluence."""
    keys = [m.key for m in table.metrics]
    x = "n" if "n" in keys else (keys[0] if keys else "")
    y = next((m.key for m in table.metrics if m.channel and m.kind == "value"), None)
    if y is None:
        y = next((k for k in keys if k != x), x)
    return x, y
