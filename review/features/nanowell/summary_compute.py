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
    the fraction responding is undefined rather than guessed.
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
    frame = metrics.assign(_value=pd.to_numeric(metrics[metric], errors="coerce") if metric != "occupancy_class"
                           else metrics[metric], _reason=exclusion_reason(metrics, metric, channels))
    rows = []
    for (well, tp), group in frame.groupby(["well", "timepoint"], sort=False):
        values = group["_value"].dropna()
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        counted = group["n"].dropna()
        reasons = group.loc[group["_value"].isna(), "_reason"].value_counts()
        rows.append({
            "well": well, "timepoint": tp, "time_h": group["time_h"].iloc[0], "tp_order": group["tp_order"].iloc[0],
            "n_sites": len(group), "n_used": len(values), "excluded": {str(k): int(v) for k, v in reasons.items()},
            "median": float(numeric.median()) if len(numeric) else math.nan,
            "q1": float(numeric.quantile(0.25)) if len(numeric) else math.nan,
            "q3": float(numeric.quantile(0.75)) if len(numeric) else math.nan,
            "fraction_responding": (float((numeric > threshold).mean())
                                    if threshold is not None and len(numeric) else math.nan),
            "fraction_occupied": float((counted > 0).mean()) if len(counted) else math.nan,
        })
    columns = ["well", "timepoint", "time_h", "tp_order", "n_sites", "n_used", "excluded", "median", "q1", "q3",
               "fraction_responding", "fraction_occupied"]
    return pd.DataFrame(rows, columns=columns).sort_values(["well", "tp_order"], kind="stable").reset_index(drop=True)


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
    for name, members in groups.items():
        mine = wells[wells["well"].isin(list(members))]
        for tp, part in mine.groupby("timepoint", sort=False):
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
            rows.append({"group": name, "timepoint": tp, "time_h": part["time_h"].iloc[0],
                         "tp_order": part["tp_order"].iloc[0], "n_wells": k,
                         "wells": sorted(part.loc[part["median"].notna(), "well"]),
                         "wells_without_value": sorted(part.loc[part["median"].isna(), "well"]),
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
