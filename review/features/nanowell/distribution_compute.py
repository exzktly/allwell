"""What the Distribution view draws, as data: panels, swarms, the per-well line and statistics, and every
exclusion counted by reason (tracker V3b, V3d, V3e; decisions U1, U2, U3, U11, U13).

The renderer turns a :class:`DistributionModel` into a figure and adds nothing of its own, so everything a
reader could count on the figure — dots, exclusions, the used total, the statistic over wells — is decided
here and testable without a screen.

**Two layouts, one figure** (U3). *Along x: Time* draws one panel per well — or, when replicate sets are
defined, one panel per set with each member well its own sub-swarm (U2) — and timepoints along the axis, the
well medians joined over time. *Along x: Wells* draws one panel at one timepoint (the latest by default, U13)
with each group along the axis, its member wells as sub-swarms, and the group's centre and 95% interval over
**wells** beside it (U11). Nanowells are never pooled across wells into one swarm.

**Every nanowell is accounted for** (V3e). A swarm's ``excluded`` counts its nanowells with no value by
:func:`series_compute.exclusion_reason`, labelled by :func:`reason_label`; ``drawn + hidden + excluded`` is
the swarm's roster. ``hidden`` are dots with a value that did not fit the slot, stated on the figure as
"+N more at v" (:mod:`beeswarm_compute`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.features.nanowell.series_compute import exclusion_reason
from review.features.nanowell.summary_compute import LOG_METRICS, group_summary, well_summary

TIME, WELLS = "time", "wells"

#: The page's metrics: key -> (label, needs a channel). A channel metric's column is ``<key>_<channel>``.
METRICS = {
    "n": ("Cell count", False),
    "c": ("Confluence", False),
    "brightness": ("Brightness (mean per-cell intensity)", True),
    "signal": ("Site signal (integrated, above background)", True),
    "count_fc": ("Count fold change", False),
    "log2_count_fc": ("log₂ count fold change, (n + 1) / (n₀ + 1)", False),
    "confluence_fc": ("Confluence fold change", False),
    "brightness_fc": ("Brightness fold change", True),
    "signal_fc": ("Site-signal fold change", True),
    "growth_rate": ("Growth rate (per hour, from ln n)", False),
    "occupancy_class": ("Occupancy at baseline (cells)", False),
}
def metric_labels() -> dict:
    """``metric -> label``, in the page's order — for a caller outside this feature (Batch Export) that reaches
    it through a lazy proxy, which can only call."""
    return {key: label for key, (label, _needs) in METRICS.items()}


#: Metrics with one value per nanowell, not per timepoint: drawn across wells only (interface plan §6.2 #4).
PER_NANOWELL = frozenset({"growth_rate", "occupancy_class"})
#: Metrics that depend on the baseline (the Baseline control is greyed for the others).
BASELINE_METRICS = frozenset({"count_fc", "log2_count_fc", "confluence_fc", "brightness_fc", "signal_fc",
                              "occupancy_class"})
_OCCUPANCY_VALUE = {"0": 0.0, "1": 1.0, "2": 2.0, "3+": 3.0}
#: Metrics whose values are whole numbers by definition, packed into bands (U1). Decided by the metric, not by
#: the values: a fold change on one timepoint is all 1.0 and is still a continuous quantity.
INTEGER_METRICS = frozenset({"n", "occupancy_class"})
#: The timepoint label the per-nanowell metrics are summarised under (each well's own first timepoint).
PER_NANOWELL_LABEL = "per nanowell"

#: Short labels for :func:`series_compute.exclusion_reason`'s strings, as the figure prints them.
#: The reason a value is left out under the fixed cohort (tracker I8): its nanowell lacks one at some timepoint.
COHORT_REASON = "not measured at every timepoint"

_REASON_LABELS = {
    "occluded": "occluded", "clipped": "clipped", "unaddressed": "no address", "not_located": "not located",
    "absent": "not in roster", "never measured": "never measured",
    "fused (no count)": "fused, no count", "no count": "no count", "no confluence": "no confluence",
    "no cells": "no cells", "no fluorescence": "no fluorescence",
    "seeded later": "seeded later (0 at baseline)", "empty at baseline": "empty at baseline",
    "no confluence at baseline": "no confluence at baseline",
    "no signal above background at baseline": "no signal at baseline",
    "fewer than three timepoints with cells and a time": "< 3 timepoints with cells",
    "no count at baseline": "no count at baseline",
    COHORT_REASON: "outside the fixed cohort",
}


def reason_label(reason: str) -> str:
    """The figure's label for an exclusion reason. Raises ``KeyError`` for one it has never seen, so a new
    reason cannot reach a figure unlabelled (a test enumerates every reason the data layer can produce)."""
    if reason in _REASON_LABELS:
        return _REASON_LABELS[reason]
    if reason.startswith("baseline: "):
        return "baseline: " + _REASON_LABELS[reason[len("baseline: "):]]
    if reason.startswith("baseline "):
        return "baseline " + _REASON_LABELS[reason[len("baseline "):]]
    raise KeyError(reason)


def metric_column(metric: str, channel: str | None) -> str:
    """The metrics-frame column for a page metric; a channel metric needs a channel."""
    label, needs = METRICS[metric]
    if not needs:
        return metric
    if not channel:
        raise ValueError(f"{label} needs a fluorescence channel")
    return f"{metric}_{channel}"


def match_channel(active: str | None, channels) -> str | None:
    """The data's channel spelled as the app's shared selector names it (case-insensitive), or ``None``."""
    wanted = (active or "").strip().lower()
    return next((c for c in channels if c.lower() == wanted), None)


@dataclass
class Swarm:
    label: str                       # the timepoint (time axis) or the well (wells axis)
    well: str
    x: float                         # the slot's centre on the panel's x axis
    values: np.ndarray               # the metric, one per nanowell with a value
    sites: list                      # (well, q, r) for each value
    occupancy: list                  # occupancy class for each value ("0" / "1" / "2" / "3+" / None)
    n_roster: int
    excluded: dict = field(default_factory=dict)       # label -> count

    @property
    def n_excluded(self) -> int:
        return int(sum(self.excluded.values()))


@dataclass
class GroupStat:
    group: str
    x: float
    centre: float
    ci_low: float
    ci_high: float
    n_wells: int
    df: int | None
    vs_control: float
    wells_without_value: list


@dataclass
class Panel:
    title: str
    swarms: list
    ticks: list                      # (x, label)
    medians: dict = field(default_factory=dict)        # well -> [(x, median)], joined over time
    stats: list = field(default_factory=list)          # GroupStat, wells axis only


@dataclass
class DistributionModel:
    metric: str
    column: str
    label: str
    axis: str
    integer: bool
    panels: list
    timepoint: str = ""
    control: str = ""
    baseline: str = "earliest"
    notes: list = field(default_factory=list)          # sentences printed in the footer
    #: The cut the user set for this metric (tracker I9), or ``None``: then no fraction responding is computed.
    responding_above: float | None = None
    #: ``(group, fraction, timepoint)`` per group: the mean of its wells' fractions responding at *timepoint*.
    responding: list = field(default_factory=list)

    def swarms(self):
        return [s for p in self.panels for s in p.swarms]

    @property
    def n_roster(self) -> int:
        return sum(s.n_roster for s in self.swarms())

    @property
    def n_used(self) -> int:
        return sum(len(s.values) for s in self.swarms())

    def excluded(self) -> dict:
        out: dict[str, int] = {}
        for s in self.swarms():
            for k, v in s.excluded.items():
                out[k] = out.get(k, 0) + v
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _values(rows: pd.DataFrame, column: str) -> pd.Series:
    if column == "occupancy_class":
        return rows[column].map(_OCCUPANCY_VALUE).astype(float)
    return pd.to_numeric(rows[column], errors="coerce")


def _swarm(rows: pd.DataFrame, column: str, reasons: pd.Series, *, label: str, well: str, x: float) -> Swarm:
    values = _values(rows, column)
    has = values.notna()
    excluded: dict[str, int] = {}
    for reason, count in reasons[~has].value_counts().items():
        excluded[reason_label(str(reason))] = excluded.get(reason_label(str(reason)), 0) + int(count)
    kept = rows[has]
    occupancy = [None if pd.isna(v) else str(v) for v in kept["occupancy_class"]]
    sites = list(zip(kept["well"], kept["nw_q"].astype(int), kept["nw_r"].astype(int)))
    return Swarm(label, well, x, values[has].to_numpy(dtype=float), sites, occupancy, len(rows), excluded)


def fixed_cohort(frame: pd.DataFrame, column: str, reasons: pd.Series) -> tuple[pd.DataFrame, pd.Series, int, int]:
    """Only the nanowells with a value at every timepoint of their well (tracker I8): ``(frame, reasons, kept,
    sites)``. A value of any other nanowell becomes undefined with :data:`COHORT_REASON`, so it is counted among
    the exclusions rather than vanishing, and every line over time follows one set of nanowells — without it the
    median at each timepoint is over whichever nanowells could be measured then, which can move with no
    nanowell changing."""
    values = pd.to_numeric(frame[column], errors="coerce") if column != "occupancy_class" else frame[column]
    has = values.notna()
    key = [frame["well"], frame["nw_q"], frame["nw_r"]]
    complete = has.groupby(key).transform("all")
    out = has & ~complete
    frame = frame.copy()
    frame.loc[out, column] = np.nan
    reasons = reasons.copy()
    reasons[out] = COHORT_REASON
    sites = frame.drop_duplicates(["well", "nw_q", "nw_r"])
    kept = int(complete.groupby(key).first().sum())
    return frame, reasons, kept, len(sites)


def wells_summary(metrics: pd.DataFrame, wells, metric: str, *, channels=(), channel: str | None = None,
                  cohort: bool = False, responding_above: float | None = None) -> pd.DataFrame:
    """``summary_compute.well_summary`` for *wells*, prepared exactly as the Distribution view prepares it: a
    per-nanowell metric at each well's own first timepoint, labelled :data:`PER_NANOWELL_LABEL`, and the fixed
    cohort applied when *cohort* (its exclusions then counted under :data:`COHORT_REASON`). Batch Export's
    Nanowells mode writes this beside each figure (tracker I4), so the CSV's numbers are the figure's.
    *responding_above* is the user's cut for the metric; without one the fraction responding is undefined."""
    column = metric_column(metric, channel)
    frame = metrics[metrics["well"].isin(list(wells))]
    reasons = exclusion_reason(frame, column, channels)
    if metric in PER_NANOWELL:
        first = frame["tp_order"] == 0
        frame, reasons = frame[first].assign(timepoint=PER_NANOWELL_LABEL), reasons[first]
    elif cohort:
        frame, reasons, _kept, _sites = fixed_cohort(frame, column, reasons)
    return well_summary(frame, column, channels, reasons=reasons, responding_above=responding_above)


def distribution_model(metrics: pd.DataFrame, metric: str, *, channels=(), channel: str | None = None,
                       axis: str = TIME, groups: dict, timepoint: str = "", control: str = "",
                       time_source: str | None = None, baseline: str = "earliest",
                       cohort: bool = False, responding_above: float | None = None) -> DistributionModel:
    """The Distribution view for *groups* (``name -> wells``, as ``controller.replicate_groups`` gives).

    A group of one well is that well; a group of several is a replicate set. Only the wells named in
    *groups* are drawn. *responding_above* is the cut the user set for this metric (tracker I9): the footer
    states each group's fraction responding at it, or says that no cut is set.
    """
    column = metric_column(metric, channel)
    label = METRICS[metric][0] + (f" · {channel}" if METRICS[metric][1] else "")
    wells = [w for members in groups.values() for w in members]
    frame = metrics[metrics["well"].isin(wells)]
    reasons = exclusion_reason(frame, column, channels)
    if metric in PER_NANOWELL:
        # One row per nanowell: each well's own first timepoint (``tp_order`` is per well), summarised under one
        # label so wells imaged from different first timepoints are still one comparison.
        axis = WELLS
        first = frame["tp_order"] == 0
        frame, reasons = frame[first].assign(timepoint=PER_NANOWELL_LABEL), reasons[first]
    model = DistributionModel(metric, column, label, axis, metric in INTEGER_METRICS, [], control=control,
                              baseline=baseline, responding_above=responding_above)
    if cohort and metric not in PER_NANOWELL:
        frame, reasons, kept, sites = fixed_cohort(frame, column, reasons)
        model.notes.append(f"Fixed cohort: only the {kept:,} of {sites:,} nanowells with a value at every timepoint "
                           "of their well are used; the rest are counted as outside the fixed cohort.")
    if axis == TIME:
        _time_panels(model, frame, column, reasons, groups, channels)
    else:
        _wells_panel(model, frame, column, reasons, groups, timepoint, control, channels)
    _notes(model, metric, time_source)
    return model


def _order(frame: pd.DataFrame) -> list[str]:
    tps = frame.drop_duplicates("timepoint").sort_values(["time_h", "tp_order", "timepoint"], na_position="last")
    return list(tps["timepoint"])


def _time_label(frame: pd.DataFrame, tp: str) -> str:
    hours = frame.loc[frame["timepoint"] == tp, "time_h"]
    h = hours.iloc[0] if len(hours) else np.nan
    return f"{h:g} h · {tp}" if pd.notna(h) and f"{h:g} h" != tp and f"{h:g}h" != tp else str(tp)


def _time_panels(model, frame, column, reasons, groups, channels) -> None:
    summary = (well_summary(frame, column, channels, reasons=reasons, responding_above=model.responding_above)
               if len(frame) else None)
    for name, members in groups.items():
        rows = frame[frame["well"].isin(members)]
        tps = _order(rows)
        k = len(members)
        width = 1.0 / k
        panel = Panel(title=name if k == 1 else f"{name} ({k} wells)", swarms=[],
                      ticks=[(float(i), _time_label(rows, tp)) for i, tp in enumerate(tps)])
        for i, tp in enumerate(tps):
            for j, well in enumerate(members):
                sel = (rows["timepoint"] == tp) & (rows["well"] == well)
                if not sel.any():
                    continue
                x = i + (j - (k - 1) / 2) * width
                panel.swarms.append(_swarm(rows[sel], column, reasons[rows.index[sel]], label=tp, well=well, x=x))
        if summary is not None:
            for j, well in enumerate(members):
                mine = summary[summary["well"] == well].set_index("timepoint")
                panel.medians[well] = [(i + (j - (k - 1) / 2) * width, float(mine.loc[tp, "median"]))
                                       for i, tp in enumerate(tps) if tp in mine.index]
            if tps:
                # Over time, each group's fraction is stated at its latest timepoint: one number per group.
                last = summary[summary["well"].isin(members) & (summary["timepoint"] == tps[-1])]
                fractions = last["fraction_responding"].dropna()
                model.responding.append((name, float(fractions.mean()) if len(fractions) else math.nan, tps[-1]))
        model.panels.append(panel)


def _wells_panel(model, frame, column, reasons, groups, timepoint, control, channels) -> None:
    tps = _order(frame)
    if model.metric not in PER_NANOWELL:
        timepoint = timepoint if timepoint in tps else (tps[-1] if tps else "")     # the latest by default (U13)
        frame = frame[frame["timepoint"] == timepoint]
        reasons = reasons[frame.index]
    model.timepoint = timepoint if model.metric not in PER_NANOWELL else ""
    panel = Panel(title=(f"{timepoint}" if model.timepoint else "One value per nanowell"), swarms=[], ticks=[])
    summary = (well_summary(frame, column, channels, reasons=reasons, responding_above=model.responding_above)
               if len(frame) else pd.DataFrame(columns=["well", "timepoint", "median", "n_used", "excluded", "time_h",
                                                        "tp_order", "fraction_responding"]))
    stats = group_summary(summary, groups, metric=column if column in LOG_METRICS else "",
                          control=control if control in groups else None)
    for i, (name, members) in enumerate(groups.items()):
        k = len(members)
        width = 0.8 / max(k, 1)
        panel.ticks.append((float(i), name))
        for j, well in enumerate(members):
            sel = frame["well"] == well
            if not sel.any():
                continue
            x = i + (j - (k - 1) / 2) * width
            panel.swarms.append(_swarm(frame[sel], column, reasons[frame.index[sel]], label=well, well=well, x=x))
        row = stats[stats["group"] == name]
        if len(row):
            r = row.iloc[0]
            panel.stats.append(GroupStat(name, float(i), float(r["centre"]), float(r["ci_low"]), float(r["ci_high"]),
                                         int(r["n_wells"]), int(r["n_wells"]) - 1 if r["n_wells"] >= 2 else None,
                                         float(r["vs_control"]), list(r["wells_without_value"])))
            model.responding.append((name, float(r["fraction_responding"]), model.timepoint or PER_NANOWELL_LABEL))
    model.panels.append(panel)


def _notes(model: DistributionModel, metric: str, time_source: str | None) -> None:
    if model.integer:
        model.notes.append("Whole-number values are packed within ±0.4 of their value.")
    if model.axis == WELLS and any(p.stats for p in model.panels):
        dfs = sorted({s.df for p in model.panels for s in p.stats if s.df is not None})
        model.notes.append("Statistics are per well (mean of well medians, 95% t interval"
                           + (f", df {', '.join(map(str, dfs))}" if dfs else "") + "); nanowells are not replicates.")
    elif any(n.startswith("Fixed cohort") for n in model.notes):
        model.notes.append("The line joins each well's median over its fixed cohort, the same nanowells at every "
                           "timepoint.")
    else:
        model.notes.append("The line joins each well's median over the nanowells with a value at that timepoint.")
    model.notes.append(responding_note(model))
    if time_source == "label":
        model.notes.append("Times are read from the timepoint labels, not the acquisition record.")
    elif time_source is None:
        model.notes.append("Times are unknown; timepoints are in label order.")


def format_cut(value: float) -> str:
    """A cut as text that reads back as exactly the same number.

    ``:g`` keeps six significant figures, so ``12345.67`` showed as ``12345.7`` — and the field, re-read on its
    next focus change, then stored the rounded number in place of the typed one. Fifteen significant figures
    hold any decimal a person types; ``repr`` is the fallback for a value that still would not round-trip.
    """
    text = f"{value:.15g}"
    return text if float(text) == value else repr(value)


def responding_note(model: DistributionModel) -> str:
    """The footer's fraction-responding sentence: each group's fraction at the user's cut, or that none is set."""
    if model.responding_above is None:
        return (f"Fraction responding is not computed: no threshold is set for {model.label} "
                "(Responding above).")
    parts = [f"{name} {fraction:.0%}" if math.isfinite(fraction) else f"{name} —"
             for name, fraction, _tp in model.responding]
    at = {tp for _n, _f, tp in model.responding}
    where = f" at {next(iter(at))}" if len(at) == 1 else " at each group's latest timepoint"
    return (f"Responding: above {format_cut(model.responding_above)}. Fraction responding (mean over wells){where}: "
            + (", ".join(parts) if parts else "no group has a value") + ".")
