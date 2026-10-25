"""The *Starting cells* filter: use only the nanowells that held a given number of cells at the baseline.

Asked for: "filter nanowells for purposes of inclusion … only compute statistics/display [nanowells] that start
with a minimum number of cells, and then use that for the nanowell montage, trajectories, distributions display".

**What "starting" means.** A nanowell's count at the page's Baseline — the well's earliest timepoint, or the
nanowell's first measurable one — which the metrics table holds as ``n0``: the same number the occupancy colours
(0 / 1 / 2 / 3+) are drawn from, so the filter and the key always agree. Under *First measurable* a nanowell
counted after the earliest timepoint may have divided already, so a "single cell" filter there is not a clonality
filter; the note counts those nanowells so the figure says so.

**The filter comes first.** The nanowells outside it are taken out of what a view computes on before any
exclusion is worked out, and every figure carries one note that counts them (:func:`note`) — rather than a reason
per swarm, which would read hundreds against every swarm and drown the reasons a reader acts on. So, under ``≥1``,
"empty at baseline" no longer appears among a fold change's exclusions: those nanowells were filtered out first,
and are counted in the note instead. A nanowell is never counted twice. A nanowell whose count at baseline is
unknown is outside any filter with a bound, and is counted as such.

Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

#: The presets the control offers: key, label, minimum, maximum (``None`` is no bound on that side).
PRESETS = (("any", "Any", None, None), ("occupied", "≥1 (occupied)", 1, None),
           ("single", "1 (single cell)", 1, 1), ("one_two", "1–2", 1, 2), ("two_plus", "≥2", 2, None))
#: The control's last entry: bounds typed by hand.
CUSTOM = "custom"
#: A drawn well with fewer nanowells in the filter than this is named on the figure: wells are the replicate unit,
#: so a well of four nanowells weighs as much in a group's statistic as one of three hundred.
FEW_BELOW = 10
SITE_KEY = ["well", "nw_q", "nw_r"]


def normalise(lo, hi) -> tuple[int | None, int | None]:
    """*lo* and *hi* as bounds the filter applies: whole numbers ≥ 0 or ``None``; a minimum of 0 is no minimum.
    Raises ``ValueError`` for anything else, or a minimum above the maximum."""
    out = []
    for value in (lo, hi):
        if value is None:
            out.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"a starting-cells bound must be a whole number of cells, not {value!r}")
        out.append(value)
    lo, hi = out
    lo = None if lo == 0 else lo
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"the minimum ({lo}) is above the maximum ({hi})")
    return lo, hi


def is_active(lo, hi) -> bool:
    return lo is not None or hi is not None


def preset_of(lo, hi) -> str:
    """The preset key the bounds are, or :data:`CUSTOM`."""
    return next((key for key, _label, a, b in PRESETS if (a, b) == (lo, hi)), CUSTOM)


def bounds_text(lo, hi) -> str:
    """The bounds as the figure writes them: ``1–2``, ``≥1``, ``1``, ``≤5``, or ``any``."""
    if lo is None and hi is None:
        return "any"
    if lo is None:
        return f"≤{hi}"
    if hi is None:
        return f"≥{lo}"
    return f"{lo}" if lo == hi else f"{lo}–{hi}"


def inside(metrics: pd.DataFrame, lo, hi) -> pd.Series:
    """For each row, whether its nanowell is in the filter: every row when no bound is set; otherwise the rows
    whose baseline count is known and within the bounds."""
    if not is_active(lo, hi):
        return pd.Series(True, index=metrics.index)
    n0 = pd.to_numeric(metrics["n0"], errors="coerce") if "n0" in metrics else pd.Series(float("nan"),
                                                                                       index=metrics.index)
    keep = n0.notna()
    if lo is not None:
        keep &= n0 >= lo
    if hi is not None:
        keep &= n0 <= hi
    return keep


def occupancy_in_filter(cls, lo, hi) -> bool:
    """Whether any nanowell of occupancy class *cls* (``"0"``, ``"1"``, ``"2"``, ``"3+"``; ``None`` for an unknown
    count) can be in the filter — for the key, which greys the classes the filter removes whole."""
    if not is_active(lo, hi):
        return True
    if cls is None:
        return False
    if cls == "3+":
        return hi is None or hi >= 3
    k = int(cls)
    return (lo is None or k >= lo) and (hi is None or k <= hi)


@dataclass
class StartingCells:
    """What the filter did to one figure's nanowells."""
    lo: int | None
    hi: int | None
    baseline: str
    total: int = 0                  # nanowells of the drawn wells
    kept: int = 0
    outside: int = 0                # with a known count outside the bounds
    unknown: int = 0                # with no count at baseline
    late: int = 0                   # kept, counted after the well's earliest timepoint (first measurable)
    per_well: dict = field(default_factory=dict)    # well -> nanowells kept
    outside_sites: frozenset = frozenset()          # (well, q, r) of every nanowell not kept

    def label(self) -> str:
        return f"Starting cells {bounds_text(self.lo, self.hi)}"


def apply(metrics: pd.DataFrame, lo, hi, baseline: str, wells=None) -> tuple[pd.DataFrame, StartingCells | None]:
    """*metrics* with only the rows of nanowells in the filter, and what was done; ``(metrics, None)`` with no
    bound set. *wells* are the drawn wells (every well in *metrics* when ``None``): each is counted, and named
    when it keeps fewer than :data:`FEW_BELOW`, even when it keeps none."""
    if not is_active(lo, hi):
        return metrics, None
    frame = metrics if wells is None else metrics[metrics["well"].isin(list(wells))]
    keep = inside(frame, lo, hi)
    sites = frame.assign(_keep=keep).drop_duplicates(SITE_KEY)
    known = pd.to_numeric(sites["n0"], errors="coerce").notna() if "n0" in sites else pd.Series(False,
                                                                                              index=sites.index)
    done = StartingCells(lo, hi, baseline, total=len(sites), kept=int(sites["_keep"].sum()),
                         unknown=int((~known).sum()))
    done.outside = done.total - done.kept - done.unknown
    names = [str(w) for w in (wells if wells is not None else frame["well"].unique())]
    counts = sites[sites["_keep"]].groupby("well").size()
    done.per_well = {w: int(counts.get(w, 0)) for w in names}
    out = sites[~sites["_keep"]]
    done.outside_sites = frozenset(zip(out["well"], out["nw_q"].astype(int), out["nw_r"].astype(int)))
    if baseline == "first_measurable" and len(frame):
        first = frame[frame["tp_order"] == frame.groupby("well")["tp_order"].transform("min")]
        earliest = dict(zip(first["well"], first["timepoint"]))
        kept = sites[sites["_keep"]]
        done.late = int((kept["baseline_timepoint"].notna()
                         & (kept["baseline_timepoint"] != kept["well"].map(earliest))).sum())
    return frame[keep.to_numpy()], done


def note(done: StartingCells) -> str:
    """The figure's sentence about the filter: the bounds, the baseline, and every nanowell left out by why."""
    where = ("each nanowell's first measurable timepoint" if done.baseline == "first_measurable"
             else "the earliest timepoint")
    said = (f"{done.label()} (count at {where}): {done.kept:,} of {done.total:,} nanowells kept — "
            f"{done.outside:,} outside the range, {done.unknown:,} with no count at baseline.")
    if done.late:
        said += (f" {done.late:,} kept were counted after the earliest timepoint, when their cells may already "
                 "have divided.")
    few = [f"{w} ({k})" for w, k in done.per_well.items() if k < FEW_BELOW]
    if few:
        said += " Few nanowells in the filter: " + ", ".join(few) + "."
    return said


def empty_text(done: StartingCells) -> str:
    """What a figure says when the filter keeps no nanowell of the drawn wells."""
    where = ("each nanowell's first measurable timepoint" if done.baseline == "first_measurable"
             else "the earliest timepoint")
    bounds = bounds_text(done.lo, done.hi)
    return (f"No nanowell of the drawn wells started with {bounds} {'cell' if bounds == '1' else 'cells'} "
            f"(count at {where}). Widen Starting cells or choose Any.")
