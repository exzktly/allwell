"""A well's nanowells summarised, and wells compared (tracker §3.10.2; rows D5, D6, D7).

**Wells are the replicate unit, not nanowells.** A thousand nanowells in one well share its medium, seeding
and handling; treating them as independent replicates in a well-versus-well comparison would report a
confidence nobody has. So the two steps here are deliberately separate, and the second cannot see a site:

1. :func:`well_summary` — for each (well, timepoint), the distribution of a metric over the well's nanowells:
   median, interquartile range, fraction responding, fraction occupied, the number of nanowells used and the
   number excluded *by reason*. This is a description of one well.
2. :func:`group_summary` — for each replicate group and timepoint, a centre and interval computed from **one
   value per well** (that well's median). It takes the output of step 1, which has one row per well, so
   pooling nanowells as replicates is not something it can be asked to do.

**Versus control** follows ``review/domain/fold_change.py``'s rule that the numerator and the denominator use
the same statistic: a group's centre (the mean of its wells' medians) over the control group's centre computed
the same way, at the same timepoint. On the log₂ fold-change metric the comparison is the difference of the
two centres, the ratio's logarithm. A control centre at or below zero leaves the comparison undefined.

Qt-free.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from review.features.nanowell.series_compute import exclusion_reason

#: Metrics read on a log scale: compared to control by difference, and "responding" above 0.
LOG_METRICS = frozenset({"log2_count_fc"})


def responding_threshold(metric: str) -> float | None:
    """The value above which a nanowell counts as responding: 1 for a fold change, 0 for a log₂ fold change.

    A measurement (a count, a confluence, an intensity) has no natural "responding" cut, so it has none and
    the fraction responding is undefined rather than guessed. A nanowell seeded later (empty at baseline,
    cells afterwards) has no count fold change, so it is not among those responding on ``count_fc`` — it is
    counted under "seeded later" in the exclusions — while ``log2_count_fc`` (defined with the +1) counts it.
    """
    if metric in LOG_METRICS:
        return 0.0
    if "_fc" in metric:
        return 1.0
    return None


def well_summary(metrics: pd.DataFrame, metric: str, channels=(), *,
                 responding_above: float | None = None) -> pd.DataFrame:
    """One row per (well, timepoint): the metric's distribution over the well's nanowells.

    ``n_sites`` is the roster; ``n_used`` the nanowells with a value; ``excluded`` maps each reason (from
    :func:`series_compute.exclusion_reason`) to its count, so ``n_used + sum(excluded) == n_sites``.
    ``fraction_occupied`` is over nanowells with a count at that timepoint, whatever the metric.
    """
    threshold = responding_threshold(metric) if responding_above is None else responding_above
    # Occupancy classes are ordinal; "3+" stands at 3 for the median and quartiles, as the class it is.
    values = (metrics[metric].map({"0": 0.0, "1": 1.0, "2": 2.0, "3+": 3.0}) if metric == "occupancy_class"
              else pd.to_numeric(metrics[metric], errors="coerce"))
    keys = ["well", "timepoint"]
    columns = ["well", "timepoint", "time_h", "tp_order", "n_sites", "n_used", "excluded", "median", "q1", "q3",
               "fraction_responding", "fraction_occupied"]
    if not len(metrics):
        return pd.DataFrame(columns=columns)
    # Vectorised over every (well, timepoint) at once: the page calls this on each redraw, and a Python loop
    # over a plate's groups took seconds on the GUI thread.
    frame = metrics[keys + ["time_h", "tp_order", "n"]].assign(_value=values.astype(float))
    grouped = frame.groupby(keys, sort=False)
    out = grouped.agg(time_h=("time_h", "first"), tp_order=("tp_order", "first"), n_sites=("_value", "size"),
                      n_used=("_value", "count"), median=("_value", "median"))
    quantiles = grouped["_value"].quantile([0.25, 0.75]).unstack()
    out["q1"], out["q3"] = quantiles[0.25], quantiles[0.75]
    has = frame["_value"].notna()
    if threshold is None:
        out["fraction_responding"] = math.nan
    else:
        above = (frame["_value"] > threshold).where(has).astype(float)
        out["fraction_responding"] = above.groupby([frame[k] for k in keys], sort=False).mean()
    counted = frame["n"].notna()
    occupied = (frame["n"] > 0).where(counted).astype(float)
    out["fraction_occupied"] = occupied.groupby([frame[k] for k in keys], sort=False).mean()
    missing = ~has
    reasons = exclusion_reason(metrics, metric, channels)[missing]   # on the whole frame: a reason reads the baseline row
    counts = (pd.DataFrame({"well": frame.loc[missing, "well"], "timepoint": frame.loc[missing, "timepoint"],
                            "reason": reasons}).groupby(keys + ["reason"], sort=False).size())
    excluded: dict = {}
    for (well, tp, reason), n in counts.items():
        excluded.setdefault((well, tp), {})[str(reason)] = int(n)
    out = out.reset_index()
    out["excluded"] = [excluded.get((w, t), {}) for w, t in zip(out["well"], out["timepoint"])]
    out["n_sites"] = out["n_sites"].astype(int)
    out["n_used"] = out["n_used"].astype(int)
    return out[columns].sort_values(["well", "tp_order"], kind="stable").reset_index(drop=True)


def _t95(dof: int) -> float:
    from scipy import stats

    return float(stats.t.ppf(0.975, dof))


def group_summary(wells: pd.DataFrame, groups: dict, *, metric: str = "", control: str | None = None) -> pd.DataFrame:
    """One row per (group, timepoint): a centre and interval over **wells** (tracker D6, D7).

    *wells* is :func:`well_summary`'s output; *groups* maps a group name to its wells (Sample Definitions'
    replicate sets, or one well per group). Each well contributes its median, once. ``centre`` is the mean of
    those medians, ``sd`` / ``sem`` their spread, and ``ci_low`` / ``ci_high`` a 95% Student-t interval
    (undefined with fewer than two wells). ``n_wells`` counts wells with a value; ``wells_without_value``
    names the rest. ``vs_control`` compares to *control*'s centre (see the module docstring).
    """
    rows = []
    timeline = wells.drop_duplicates("timepoint").sort_values(["time_h", "tp_order", "timepoint"], kind="stable",
                                                              na_position="last")
    for name, members in groups.items():
        mine = wells[wells["well"].isin(list(members))]
        for tp, time_h, tp_order in zip(timeline["timepoint"], timeline["time_h"], timeline["tp_order"]):
            part = mine[mine["timepoint"] == tp]
            # A member with no row here (no addressed nanowell, or not imaged at this timepoint) is a well
            # without a value, named, not a well that was never in the group.
            absent = sorted(set(members) - set(part["well"]))
            values = part["median"].dropna()
            k = len(values)
            centre = float(values.mean()) if k else math.nan
            sd = float(values.std(ddof=1)) if k >= 2 else math.nan
            sem = sd / math.sqrt(k) if k >= 2 else math.nan
            half = _t95(k - 1) * sem if k >= 2 else math.nan
            excluded: dict[str, int] = {}
            for counts in part["excluded"]:
                for reason, n in counts.items():
                    excluded[reason] = excluded.get(reason, 0) + n
            rows.append({"group": name, "timepoint": tp, "time_h": time_h, "tp_order": tp_order, "n_wells": k,
                         "wells": sorted(part.loc[part["median"].notna(), "well"]),
                         "wells_without_value": sorted(set(part.loc[part["median"].isna(), "well"]) | set(absent)),
                         "centre": centre, "sd": sd, "sem": sem, "ci_low": centre - half, "ci_high": centre + half,
                         "n_sites_used": int(part["n_used"].sum()), "excluded": excluded})
    columns = ["group", "timepoint", "time_h", "tp_order", "n_wells", "wells", "wells_without_value", "centre",
               "sd", "sem", "ci_low", "ci_high", "n_sites_used", "excluded"]
    out = pd.DataFrame(rows, columns=columns)
    out["vs_control"] = np.nan
    if control is not None and len(out):
        reference = out[out["group"] == control].set_index("timepoint")["centre"]
        bottom = out["timepoint"].map(reference).astype(float)
        if metric in LOG_METRICS:
            out["vs_control"] = (out["centre"] - bottom).where(bottom.notna())
        else:
            ok = bottom.notna() & (bottom > 0)
            out["vs_control"] = (out["centre"] / bottom.where(ok)).where(ok)
    order = {name: i for i, name in enumerate(groups)}             # the groups in the order they were given
    out = out.assign(_order=out["group"].map(order))
    return out.sort_values(["_order", "tp_order"], kind="stable").drop(columns="_order").reset_index(drop=True)
