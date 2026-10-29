"""Each nanowell joined over time, and the per-nanowell metrics of tracker §3.10.1 (rows D2, D2a, D3, D4).

A nanowell is followed across timepoints by its address ``(well, q, r)``, which the pipeline fixes to the
array, not to the image, so the join holds even when the plate moved between acquisitions.

**Every timepoint of the well is present for every nanowell.** A timepoint at which a nanowell has no
number is a row with the state that explains why — ``occluded`` or ``clipped`` as recorded, ``unaddressed``
(the timepoint was located but given no origin, so no row can be joined to an address), ``not_located``
(the timepoint failed before any site was found), ``absent`` (addressed, but this site is not in its
roster) or ``identity_contradicted`` (the identity check, ``pipeline.nanowell_identity``, found the timepoint's
addresses naming other nanowells than the well's reference timepoint, so its rows are joined to nothing). A gap therefore reads as "not seen", never as zero, and never as a shorter series.

**Undefined is NaN, never zero, and each metric's undefined cases are listed** (§3.10.1):

=====================  ==============================================  ===========================================
metric                 value                                           undefined when
=====================  ==============================================  ===========================================
``count_fc``           n(t) / n(baseline)                              n(baseline) = 0 (``seeded_later`` when n(t) >
                                                                       0), or either timepoint has no count
``log2_count_fc``      log2((n + 1) / (n₀ + 1))                        either timepoint has no count
``confluence_fc``      c(t) / c(baseline)                              c(baseline) = 0, or either is unmeasurable
``brightness_fc_<ch>`` mean per-cell intensity, t over baseline        no cells, or no fluorescence, at either
``signal_fc_<ch>``     site-integrated intensity, t over baseline      unmeasurable or no fluorescence at either,
                                                                       or no signal above background at baseline
``growth_rate``        slope of ln n against time (per hour)           fewer than three timepoints with a time and
                                                                       n > 0
``occupancy_class``    "0" / "1" / "2" / "3+" cells at baseline        no count at baseline
=====================  ==============================================  ===========================================

The nanowell fluorescence (the ``signal_<channel>`` columns) is the pipeline's interior sum *minus the empty sites'
background*, so it is near zero for
an empty nanowell and can be negative; a ratio over a baseline at or below zero has no meaning, which is the
one undefined case this table adds to §3.10.1's ("no signal above background at baseline").

**Baseline** (§3.10.1): ``"earliest"`` — the well's earliest timepoint, so a site unmeasurable there has no
fold change — or ``"first_measurable"``, the labelled alternative for a bubble that clears after t₀: for each
measurement, the site's first timepoint in a measurable state at which that measurement exists, so a count's
baseline and a confluence's can differ — or one named timepoint of the dataset (``"tp:<name>"``, built by
:func:`baseline_at`), the same for every nanowell, so a fold change can be measured from a treatment timepoint
rather than from the seeding. A well that has no such timepoint has no baseline: its fold changes are undefined
with that reason (:data:`NO_BASELINE_TIMEPOINT`), never measured from some other timepoint in its place.
``baseline_timepoint`` / ``baseline_state`` are the count's.

Qt-free; vectorised over the whole dataset (a 96-well plate of 1,000 nanowells over ten timepoints is a
million rows).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from pipeline.nanowell_identity import CONTRADICTED, NOT_CHECKED
from review.features.nanowell.collect import (
    IDENTITY_CONTRADICTED, MEASURABLE, NOT_LOCATED, UNADDRESSED, NanowellData,
)

ABSENT = "absent"
EARLIEST, FIRST_MEASURABLE = "earliest", "first_measurable"
BASELINES = (EARLIEST, FIRST_MEASURABLE)
#: The prefix of a baseline that names one timepoint (``"tp:T03"``). A prefix rather than the bare name, so a
#: timepoint that happens to be called ``earliest`` can never be read as the rule of that name.
TIMEPOINT_BASELINE = "tp:"
#: The baseline "state" of a site whose well has no timepoint by the chosen baseline's name.
NO_BASELINE_TIMEPOINT = "timepoint not in this well"


def baseline_at(timepoint: str) -> str:
    """The baseline that measures every fold change from *timepoint*."""
    return TIMEPOINT_BASELINE + str(timepoint)


def baseline_timepoint(baseline) -> str | None:
    """The timepoint *baseline* names, or ``None`` for one of the rules (:data:`BASELINES`) or anything else."""
    if isinstance(baseline, str) and baseline.startswith(TIMEPOINT_BASELINE) and len(baseline) > len(TIMEPOINT_BASELINE):
        return baseline[len(TIMEPOINT_BASELINE):]
    return None


def is_baseline(baseline) -> bool:
    """Whether *baseline* is one :func:`site_metrics` accepts: a rule of :data:`BASELINES` or a named timepoint."""
    return baseline in BASELINES or baseline_timepoint(baseline) is not None


def baseline_text(baseline) -> str:
    """How a figure names *baseline*: ``earliest``, ``first measurable``, or the timepoint's own name."""
    tp = baseline_timepoint(baseline)
    if tp is not None:
        return tp
    return "first measurable" if baseline == FIRST_MEASURABLE else "earliest"
OCCUPANCY_CLASSES = ("0", "1", "2", "3+")
SITE_KEY = ["well", "nw_q", "nw_r"]


def _grid_reason(block) -> str:
    """The grid record's reason for a timepoint, or what is known when it gives none."""
    block = block or {}
    if block.get("reason"):
        return str(block["reason"])
    status = block.get("status")
    if status and status != "ok":
        return f"status {status}"
    return "the grid record lists this timepoint but the table has no rows for it"


def _addressed_grid(data: NanowellData) -> pd.DataFrame:
    """Every addressed nanowell of each well at every timepoint of that well, with a state for each row."""
    sites = data.sites
    empty = sites.iloc[0:0].assign(reason=pd.Series(dtype=object), tp_order=pd.Series(dtype=int),
                                   identity=pd.Series(dtype=object), identity_reason=pd.Series(dtype=object),
                                   identity_ok=pd.Series(dtype=bool))
    if not len(sites):
        return empty
    located = sites[["well", "timepoint"]].drop_duplicates()
    order = []
    for well, tps in located.groupby("well", sort=True)["timepoint"]:
        for i, tp in enumerate(data.order(set(tps) | set(data.grids.get(well, {})))):
            order.append((well, tp, i))
    timeline = pd.DataFrame(order, columns=["well", "timepoint", "tp_order"])
    addressed = sites[sites["nw_q"].notna() & sites["nw_r"].notna()].drop(columns=["time_h"], errors="ignore")
    keys = addressed[SITE_KEY].drop_duplicates()
    if keys.empty:
        return empty
    merged = keys.merge(timeline, on="well").merge(addressed, on=SITE_KEY + ["timepoint"], how="left")

    def tag(frame) -> pd.MultiIndex:
        return pd.MultiIndex.from_arrays([frame["well"].astype(str), frame["timepoint"].astype(str)])

    here = tag(merged)
    missing = merged["state"].isna()
    in_table = pd.Series(here.isin(tag(located)), index=merged.index)
    has_address = pd.Series(here.isin(tag(addressed)), index=merged.index)
    merged.loc[missing & in_table & ~has_address, "state"] = UNADDRESSED
    merged.loc[missing & ~in_table, "state"] = NOT_LOCATED
    merged.loc[merged["state"].isna(), "state"] = ABSENT
    reasons = {(w, tp): _grid_reason(block) for w, blocks in data.grids.items() for tp, block in blocks.items()}
    explained = merged["state"].isin((UNADDRESSED, NOT_LOCATED))
    merged["reason"] = ""
    merged.loc[explained, "reason"] = [reasons.get(k) or _grid_reason(None) for k in here[explained.to_numpy()]]
    _mark_identity(merged, here, data)
    merged["time_h"] = merged["timepoint"].map(data.times_h).astype(float)
    return merged.sort_values(SITE_KEY + ["tp_order"], kind="stable").reset_index(drop=True)


def _mark_identity(merged: pd.DataFrame, here: pd.MultiIndex, data: NanowellData) -> None:
    """Add ``identity`` (the timepoint's verdict), ``identity_reason`` and ``identity_ok`` to the joined rows, in place.

    ``identity_ok`` is False on every row of a ``contradicted`` timepoint and on each nanowell a ``partial`` one leaves
    out: those rows' addresses may name another nanowell than the reference's. Nothing measured at a timepoint is
    hidden by it — a count is a count of *some* nanowell — but no comparison over time uses such a row: no fold change
    from or to it, no growth rate through it, no occupancy class taken at it (``site_metrics``)."""
    identity = getattr(data, "identity", None) or {}
    excluded = getattr(data, "identity_excluded", None) or {}
    keys = list(here)
    verdicts = [identity.get(w, {}).get(tp, (NOT_CHECKED, "")) for w, tp in keys]
    merged["identity"] = [v for v, _why in verdicts]
    merged["identity_reason"] = [why for _v, why in verdicts]
    out = [v == CONTRADICTED for v in merged["identity"]]
    if excluded:
        q = merged["nw_q"].to_numpy()
        r = merged["nw_r"].to_numpy()
        for i, (w, tp) in enumerate(keys):
            left_out = excluded.get(w, {}).get(tp)
            if left_out and (int(q[i]), int(r[i])) in left_out:
                out[i] = True
    merged["identity_ok"] = ~np.asarray(out, dtype=bool)


def _baseline_rows(frame: pd.DataFrame, baseline: str, value: str) -> pd.Series:
    """A boolean mask marking each site's baseline row for the measurement *value*.

    Under ``first_measurable`` each measurement has its own baseline: the first timepoint at which the site
    is in a measurable state *and* that value exists. A state alone is not enough — the pipeline records
    ``measured`` with no count where no empty-nanowell template could be built, and a fused nanowell has a
    confluence but no count — and a baseline chosen by state would leave the count undefined for ever.
    """
    if baseline == EARLIEST:
        return frame["tp_order"] == 0
    if baseline == FIRST_MEASURABLE:
        measurable = frame["state"].isin(MEASURABLE) & frame[value].notna()
        first = frame["tp_order"].where(measurable).groupby([frame[k] for k in SITE_KEY]).transform("min")
        return measurable & (frame["tp_order"] == first)
    tp = baseline_timepoint(baseline)
    if tp is not None:
        return frame["timepoint"].astype(str) == tp
    raise ValueError(f"baseline must be one of {BASELINES} or a timepoint, not {baseline!r}")


def _at_baseline(frame: pd.DataFrame, mask: pd.Series, column: str) -> pd.Series:
    """*column*'s value at each site's baseline row, broadcast to every row of the site (NaN when none)."""
    base = frame.loc[mask, SITE_KEY + [column]].drop_duplicates(SITE_KEY)
    merged = frame[SITE_KEY].merge(base, on=SITE_KEY, how="left")
    return pd.Series(merged[column].to_numpy(), index=frame.index)


def _ratio(top: pd.Series, bottom: pd.Series) -> pd.Series:
    """top / bottom where both are numbers and bottom > 0; NaN otherwise."""
    ok = top.notna() & bottom.notna() & (bottom > 0)
    return (top / bottom.where(ok)).where(ok)


def _growth(frame: pd.DataFrame) -> pd.Series:
    """Least-squares slope of ln n against time per site, over timepoints with a time, n > 0 and a confirmed (or not
    contradicted) identity (≥ 3 of them)."""
    usable = frame["n"].gt(0) & frame["time_h"].notna() & frame["identity_ok"]
    x = frame["time_h"].where(usable)
    y = np.log(frame["n"].where(usable))
    keys = [frame[k] for k in SITE_KEY]
    k = usable.groupby(keys).transform("sum")
    sx, sy = x.groupby(keys).transform("sum"), y.groupby(keys).transform("sum")
    sxx = (x * x).groupby(keys).transform("sum")
    sxy = (x * y).groupby(keys).transform("sum")
    denominator = k * sxx - sx * sx
    ok = (k >= 3) & (denominator.abs() > 1e-12)
    return ((k * sxy - sx * sy) / denominator.where(ok)).where(ok)


def site_metrics(data: NanowellData, baseline: str = EARLIEST) -> pd.DataFrame:
    """One row per (well, nanowell, timepoint) with the measured values and every §3.10.1 metric.

    Columns, beyond the site table's own: ``state`` (with Review's ``unaddressed`` / ``not_located`` /
    ``absent``), ``reason``, ``time_h``, ``baseline_timepoint``, ``baseline_state``, the masked values ``n``,
    ``c`` and per channel ``brightness_<ch>`` / ``signal_<ch>``, and the metrics listed in the module
    docstring.
    """
    if not is_baseline(baseline):
        raise ValueError(f"baseline must be one of {BASELINES} or a timepoint, not {baseline!r}")
    frame = _addressed_grid(data)
    measurable = frame["state"].isin(MEASURABLE)
    frame["n"] = pd.to_numeric(frame.get("n_cells"), errors="coerce").where(measurable)
    frame["c"] = pd.to_numeric(frame.get("confluence"), errors="coerce").where(measurable)
    for ch in data.channels:
        frame[f"brightness_{ch}"] = frame[f"brightness_{ch}"].where(measurable)
        frame[f"signal_{ch}"] = frame[f"{ch}_integrated"].where(measurable)
    base = {}
    for value in ["n", "c"] + [f"{kind}_{ch}" for ch in data.channels for kind in ("brightness", "signal")]:
        mask = _baseline_rows(frame, baseline, value)
        frame[f"_base_tp_{value}"] = _at_baseline(frame, mask, "timepoint")
        frame[f"_base_state_{value}"] = _at_baseline(frame, mask, "state")
        if baseline_timepoint(baseline) is not None:
            # Every addressed nanowell has a row at each of its well's timepoints, so only a well without the
            # named timepoint leaves a site with no baseline row: say so, rather than "never measured".
            frame[f"_base_state_{value}"] = frame[f"_base_state_{value}"].fillna(NO_BASELINE_TIMEPOINT)
        # A baseline whose identity is contradicted is no baseline: every fold change from it is undefined.
        base[value] = _at_baseline(frame, mask, value).where(_at_baseline(frame, mask, "identity_ok") == True)  # noqa: E712
        frame[f"_base_identity_{value}"] = _at_baseline(frame, mask, "identity_ok")
    frame["baseline_timepoint"] = frame["_base_tp_n"]           # the count's; see _baseline_rows for the others
    frame["baseline_state"] = frame["_base_state_n"]
    n0, c0 = base["n"], base["c"]
    frame["n0"] = n0
    ok = frame["identity_ok"]
    frame["count_fc"] = _ratio(frame["n"], n0).where(ok)
    frame["seeded_later"] = n0.eq(0) & frame["n"].gt(0) & ok
    both = frame["n"].notna() & n0.notna() & ok
    frame["log2_count_fc"] = np.log2((frame["n"] + 1) / (n0 + 1)).where(both)
    frame["confluence_fc"] = _ratio(frame["c"], c0).where(ok)
    for ch in data.channels:
        frame[f"brightness_fc_{ch}"] = _ratio(frame[f"brightness_{ch}"], base[f"brightness_{ch}"]).where(ok)
        frame[f"signal_fc_{ch}"] = _ratio(frame[f"signal_{ch}"], base[f"signal_{ch}"]).where(ok)
    occupancy = pd.Series(pd.NA, index=frame.index, dtype="object")
    occupancy[n0.notna()] = np.where(n0[n0.notna()] >= 3, "3+", n0[n0.notna()].astype(int).astype(str))
    frame["occupancy_class"] = occupancy
    frame["growth_rate"] = _growth(frame)
    return frame


def site_series(metrics: pd.DataFrame, well: str, q: int, r: int) -> pd.DataFrame:
    """That nanowell at every timepoint of its well, in time order (tracker D2a).

    Occluded, clipped and unaddressed timepoints are rows with their state, not omissions. An address the
    well never had returns an empty frame, not an error.
    """
    rows = metrics[(metrics["well"] == well) & (metrics["nw_q"] == q) & (metrics["nw_r"] == r)]
    return rows.sort_values("tp_order", kind="stable").reset_index(drop=True)


def crop_metric_lines(row, channel: str | None = None) -> list[str]:
    """What a crop of one nanowell at one timepoint is captioned with, one short item each: its computed
    measurements there — the cell count, the confluence as a percentage, and the shared channel's nanowell fluorescence and
    brightness when the run measured them — in that order of importance, since a crop with room for one line writes
    as many as fit and the pop-out writes them all. A
    timepoint whose state carries no number says its state instead, and a measurement that is missing is left out
    rather than written as zero.

    *row* is one row of :func:`site_series` (a mapping)."""
    state = str(row.get("state", ""))
    if state not in ("measured", "empty"):
        return [state.replace("_", " ") or "no row"]

    def value(key):
        v = row.get(key)
        return float(v) if isinstance(v, (int, float, np.floating, np.integer)) and math.isfinite(v) else None

    n, c = value("n"), value("c")
    items = []
    if n is not None:
        items.append(f"{n:g} cell" + ("" if n == 1 else "s"))
    if c is not None:
        items.append(f"{100 * c:.0f}% conf.")
    if channel:
        b, sig = value(f"brightness_{channel}"), value(f"signal_{channel}")
        if sig is not None:
            items.append(f"signal {compact_number(sig)}")
        if b is not None:
            items.append(f"bright {compact_number(b)}")
    return items


def compact_number(value: float) -> str:
    """*value* to three significant figures, thousands and millions as ``k`` and ``M`` (``5.79k``), so a caption
    under a crop stays as narrow as the crop."""
    for scale, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= scale:
            return f"{value / scale:.3g}{suffix}"
    return f"{value:.3g}"


# ── why a value is missing ───────────────────────────────────────────────────
#: The baseline "state" of a site that has no baseline at all (first-measurable, never measured).
_NEVER = "never measured"

#: metric column -> (kind, base value column). Kinds: "value" (a measurement at t), "fc" (t over baseline),
#: "site" (one number per nanowell).
def metric_kinds(channels) -> dict:
    kinds = {"n": ("value", "n"), "c": ("value", "c"), "count_fc": ("fc", "n"), "log2_count_fc": ("fc", "n"),
             "confluence_fc": ("fc", "c"), "growth_rate": ("site", "n"), "occupancy_class": ("base", "n")}
    for ch in channels:
        kinds[f"brightness_{ch}"] = ("value", f"brightness_{ch}")
        kinds[f"signal_{ch}"] = ("value", f"signal_{ch}")
        kinds[f"brightness_fc_{ch}"] = ("fc", f"brightness_{ch}")
        kinds[f"signal_fc_{ch}"] = ("fc", f"signal_{ch}")
    return kinds


def _value_reason(frame: pd.DataFrame, value: str) -> pd.Series:
    """Why the measurement *value* is missing at a row whose state is measurable."""
    if value == "n":
        return pd.Series(np.where(frame.get("fused", False) == True, "fused (no count)", "no count"),  # noqa: E712
                         index=frame.index)
    if value == "c":
        return pd.Series("no confluence", index=frame.index)
    if value.startswith("brightness_"):
        return pd.Series(np.where(frame["n"].eq(0), "no cells", "no fluorescence"), index=frame.index)
    return pd.Series("no fluorescence", index=frame.index)


def exclusion_reason(metrics: pd.DataFrame, metric: str, channels=()) -> pd.Series:
    """For each row, why *metric* is undefined there ("" where it is defined).

    The first cause in this order wins: the state at the timepoint; the value missing at the timepoint;
    then, for a fold change, the baseline's state, the value missing there, or a baseline at zero.
    """
    kind, value = metric_kinds(channels)[metric]
    defined = metrics[metric].notna()
    out = pd.Series("", index=metrics.index, dtype=object)
    state_bad = ~metrics["state"].isin(MEASURABLE)
    value_bad = metrics[value].isna()
    if kind == "site":
        out[~defined] = "fewer than three timepoints with cells and a time"
        return out
    if kind == "base":
        base_state_bad = ~metrics["baseline_state"].isin(MEASURABLE)
        out[~defined & base_state_bad] = "baseline " + metrics["baseline_state"].fillna(_NEVER).astype(str)
        out[~defined & ~base_state_bad] = "no count at baseline"
        base_foreign = ~defined & ~base_state_bad & metrics["_base_identity_n"].eq(False)
        out[base_foreign] = "baseline " + IDENTITY_CONTRADICTED
        return out
    out[~defined & state_bad] = metrics["state"].astype(str)
    rest = ~defined & ~state_bad & value_bad
    out[rest] = _value_reason(metrics, value)[rest]
    if kind == "fc":
        pending = ~defined & (out == "")
        # A row, or its baseline, whose address may name another nanowell than the reference's joins nothing.
        foreign = pending & ~metrics["identity_ok"]
        out[foreign] = IDENTITY_CONTRADICTED
        pending &= ~foreign
        base_foreign = pending & metrics[f"_base_identity_{value}"].eq(False)
        out[base_foreign] = "baseline " + IDENTITY_CONTRADICTED
        pending &= ~base_foreign
        base_state = metrics[f"_base_state_{value}"]
        base_bad = pending & ~base_state.isin(MEASURABLE)
        out[base_bad] = "baseline " + base_state.fillna(_NEVER).astype(str)[base_bad]
        pending &= ~base_bad
        base_value = _at_baseline(metrics, metrics["timepoint"] == metrics[f"_base_tp_{value}"], value)
        missing = pending & base_value.isna()
        out[missing] = "baseline: " + _value_reason(metrics, value)[missing]
        pending &= ~missing
        if value == "n":
            out[pending & metrics["n"].gt(0)] = "seeded later"
            out[pending & ~metrics["n"].gt(0)] = "empty at baseline"
        elif value == "c":
            out[pending] = "no confluence at baseline"
        else:
            out[pending] = "no signal above background at baseline"
    return out


def long_format(metrics: pd.DataFrame, channels) -> pd.DataFrame:
    """The export table (tracker D8): one row per site and timepoint, every value and metric, in a fixed order."""
    columns = (["well", "nw_q", "nw_r", "nw_ring", "timepoint", "time_h", "state", "reason", "identity",
                "identity_ok", "fused",
                "partially_visible", "imaged_fraction", "visible_fraction", "n_cells", "confluence",
                "area_est_cells", "count_disagreement"]
               + [c for ch in channels for c in (f"brightness_{ch}", f"signal_{ch}")]
               + ["baseline_timepoint", "baseline_state", "occupancy_class", "count_fc", "seeded_later",
                  "log2_count_fc", "confluence_fc"]
               + [c for ch in channels for c in (f"brightness_fc_{ch}", f"signal_fc_{ch}")]
               + ["growth_rate"])
    out = metrics.reindex(columns=columns)
    out["n_cells"] = metrics["n"]
    out["confluence"] = metrics["c"]
    return out
