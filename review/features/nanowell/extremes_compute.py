"""What the Extremes view draws, as data: one well's nanowells ranked by a fold change at one timepoint, and the
*N* largest and *N* smallest of them followed over every timepoint of the well.

Asked for: a summary of crop montages of the nanowells with the largest and the smallest fold changes of the
chosen metric over time. The ranking is **per well** — one well's nanowells share a baseline, an exposure and a
plate position, so ranking them against each other compares like with like — at the page's timepoint (the
well's latest by default, as *Along x: Wells* compares at its latest).

**Every metric is ranked by its fold change** (:data:`FOLD_CHANGE_OF`): a cell count by the count fold change,
a confluence by the confluence fold change, and so on, so the view answers "which nanowells changed most" whatever
the Metric combo is set to. A metric with one value per nanowell (growth rate, occupancy) has no fold change over
time and is ranked by the count fold change, which the figure says.

**Every nanowell is accounted for.** A nanowell with no fold change at the ranking timepoint is not ranked and is
counted under its :func:`series_compute.exclusion_reason` label, never ranked as zero. When the well has fewer
than ``2 N`` ranked nanowells they are split between the two blocks — half each, the odd one to the largest — so
no nanowell is drawn twice, and the figure states how many there were.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.features.nanowell.distribution_compute import reason_label
from review.features.nanowell.series_compute import exclusion_reason
from review.features.nanowell.state import EXTREMES_DEFAULT_N, EXTREMES_MAX_N

#: The fold change each page metric is ranked by. A fold-change metric is its own.
FOLD_CHANGE_OF = {"n": "count_fc", "c": "confluence_fc", "brightness": "brightness_fc", "signal": "signal_fc",
                  "count_fc": "count_fc", "log2_count_fc": "log2_count_fc", "confluence_fc": "confluence_fc",
                  "brightness_fc": "brightness_fc", "signal_fc": "signal_fc"}
#: What a metric without a fold change over time (one value per nanowell) is ranked by.
FALLBACK = "count_fc"
#: How many nanowells each block shows by default, and the most it can be set to (the state holds both, since it
#: refuses a saved count outside them).
DEFAULT_N, MAX_N = EXTREMES_DEFAULT_N, EXTREMES_MAX_N


@dataclass(frozen=True)
class ExtremeRow:
    site: tuple                 # (well, q, r)
    rank: int                   # 1 is the most extreme of its block
    value: float                # the fold change at the ranking timepoint
    values: dict                # timepoint -> the fold change there (NaN where it is undefined)


@dataclass
class ExtremesModel:
    well: str
    timepoint: str              # the timepoint the nanowells are ranked at
    column: str                 # the metrics column ranked by
    n: int                      # how many each block was asked for
    timepoints: list            # the well's timepoints, in order: the montage's columns
    largest: list = field(default_factory=list)
    smallest: list = field(default_factory=list)
    roster: int = 0             # the well's nanowells at the ranking timepoint
    ranked: int = 0             # those with a fold change there
    excluded: dict = field(default_factory=dict)     # reason label -> count
    #: What the figure calls the well (``controller.well_name``); ``""`` prints the well itself.
    name: str = ""

    def sites(self) -> list:
        """Every nanowell the figure draws, the largest first."""
        return [row.site for row in self.largest + self.smallest]


def fold_change_metric(metric: str) -> tuple[str, bool]:
    """``(the metric ranked by, whether it stands in for one without a fold change)`` for a page metric."""
    if metric in FOLD_CHANGE_OF:
        return FOLD_CHANGE_OF[metric], False
    return FALLBACK, True


def clamp_n(value) -> int:
    """*value* as a block size the view can draw, :data:`DEFAULT_N` for anything that is not a whole number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return DEFAULT_N
    return int(min(max(int(value), 1), MAX_N))


def extremes(metrics: pd.DataFrame, well: str, timepoint: str, column: str, n: int, channels=()) -> ExtremesModel:
    """Rank *well*'s nanowells by *column* at *timepoint* and take the *n* largest and the *n* smallest.

    Ties are broken by address (``q``, then ``r``), so the same data always picks the same nanowells. A row
    without an address cannot be followed over time and is neither ranked nor counted.
    """
    n = clamp_n(n)
    frame = metrics[metrics["well"] == well] if len(metrics) else metrics
    frame = frame[frame["nw_q"].notna() & frame["nw_r"].notna()] if len(frame) else frame
    model = ExtremesModel(well=well, timepoint=timepoint, column=column, n=n,
                          timepoints=_timepoints(frame))
    if not len(frame) or column not in frame.columns:
        return model
    at = frame[frame["timepoint"] == timepoint]
    model.roster = len(at)
    values = pd.to_numeric(at[column], errors="coerce").astype(float)
    defined = np.isfinite(values.to_numpy())
    model.ranked = int(defined.sum())
    if (~defined).any():
        # Over the whole well: a fold change's reason reads the nanowell's baseline row, at another timepoint.
        reasons = exclusion_reason(frame, column, channels).loc[at.index][~defined]
        for reason, count in reasons.map(lambda r: reason_label(r) if r else "no value").value_counts().items():
            model.excluded[str(reason)] = int(count)
    ranked = at[defined].assign(_value=values[defined])
    # Largest first; equal values in address order, so a tie never reorders between draws.
    ranked = ranked.sort_values(["_value", "nw_q", "nw_r"], ascending=[False, True, True], kind="mergesort")
    # A well with fewer than 2n splits them between the blocks, the odd one to the largest.
    small = min(n, len(ranked) // 2)
    top = ranked.head(min(n, len(ranked) - small))
    rest = ranked.iloc[len(top):]
    bottom = rest.sort_values(["_value", "nw_q", "nw_r"], ascending=[True, True, True], kind="mergesort")
    bottom = bottom.head(small)
    series = _series(frame, column)
    model.largest = _rows(top, well, series)
    model.smallest = _rows(bottom, well, series)
    return model


def _timepoints(frame: pd.DataFrame) -> list:
    if not len(frame):
        return []
    order = frame[["timepoint", "tp_order"]].drop_duplicates().sort_values("tp_order")
    return list(dict.fromkeys(order["timepoint"]))


def _series(frame: pd.DataFrame, column: str) -> dict:
    """``(q, r) -> {timepoint: value}`` of *column* over the well's timepoints."""
    out: dict = {}
    for q, r, tp, value in zip(frame["nw_q"], frame["nw_r"], frame["timepoint"],
                               pd.to_numeric(frame[column], errors="coerce").astype(float)):
        out.setdefault((int(q), int(r)), {})[tp] = float(value)
    return out


def _rows(frame: pd.DataFrame, well: str, series: dict) -> list:
    rows = []
    for rank, (q, r, value) in enumerate(zip(frame["nw_q"], frame["nw_r"], frame["_value"]), 1):
        site = (well, int(q), int(r))
        rows.append(ExtremeRow(site=site, rank=rank, value=float(value), values=dict(series.get(site[1:], {}))))
    return rows


def format_value(column: str, value: float) -> str:
    """A fold change as the figure writes it: ``2.31×``, or ``+1.20`` on the log₂ scale; ``—`` when undefined."""
    if not isinstance(value, float) or not math.isfinite(value):
        return "—"
    if column.startswith("log2_"):
        return f"{value:+.3g}"
    return f"{value:.3g}×"


def caption(model: ExtremesModel, label: str) -> str:
    """What the figure says about the ranking: how many nanowells were ranked of how many, why the others were not,
    and when the blocks are short because the well has too few."""
    said = [f"{model.ranked} of {model.roster} nanowells in {model.well} have a {label.lower()} at "
            f"{model.timepoint}"]
    if model.excluded:
        said.append("not ranked: " + ", ".join(f"{count} {reason}" for reason, count in
                                               sorted(model.excluded.items(), key=lambda kv: (-kv[1], kv[0]))))
    if model.ranked < 2 * model.n:
        said.append(f"fewer than {2 * model.n} ranked, so they are split between the two blocks and none is "
                    f"shown twice")
    return "; ".join(said)
