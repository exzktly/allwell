"""Scatter of nanowell observations: any nanowell metric against any other (NANOWELL_OBSERVATIONS_TRACKER #9).

With the band's Observations set to Nanowells, a point is one nanowell at one timepoint — or, under
**Points: per well**, one well's median over its nanowells at one timepoint. The rows are the Nanowells feature's
(``review/data/observations``); nothing here computes a metric, so a value on this plot is the value on the page.

A row is drawn only where both metrics are defined; the rest are counted by why (the first axis's reason first),
and the footer says so (``observation.footer_line``). Wells stay the replicates: a per-well point is a well's
median, and a replicate set is drawn as its wells' points in one colour — nanowells are never pooled across
wells into one statistic here.

Qt-free.
"""

from __future__ import annotations

from domain.tp_match import tp_close
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.domain.observation import NANOWELLS, SHARE_METRICS, ObservationTable, footer_line


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
    scope = frame["well"].isin(wells) & _in_timepoints(frame, timepoints_h) if len(frame) else pd.Series(dtype=bool)
    rows = frame[scope]
    x, y = table.values(x_key)[scope], table.values(y_key)[scope]
    both = x.notna() & y.notna()
    sub = ObservationTable(table.observations, rows, table.metrics, table.why_missing and (
        lambda key: table.why_missing(key)[scope]), table.notes)
    excluded = sub.exclusions(x_key, y_key)
    series = []
    for group in groups:
        mine = rows["well"].isin(group.wells) & both
        if per_well:
            medians = (pd.DataFrame({"well": rows["well"], "timepoint": rows["timepoint"], "x": x, "y": y})[mine]
                       .groupby(["well", "timepoint"], sort=True)
                       .agg({"x": "mean" if x_key in SHARE_METRICS else "median",
                             "y": "mean" if y_key in SHARE_METRICS else "median"}))
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
    return NanowellScatter(series, x_label, y_label, footer, table.notes)


def default_axes(table: ObservationTable) -> tuple[str, str]:
    """Two different metrics to open on: the cell count against the first fluorescence metric, else confluence."""
    keys = [m.key for m in table.metrics]
    x = "n" if "n" in keys else (keys[0] if keys else "")
    y = next((m.key for m in table.metrics if m.channel and m.kind == "value"), None)
    if y is None:
        y = next((k for k in keys if k != x), x)
    return x, y
