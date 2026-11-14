"""Rare-responder analysis, part 1: the per-nanowell table, its quality control, loading strata and well summary.

The rare-responder analysis (``rare_report_compute.analyse``) asks whether a treatment well holds a *handful* of
nanowells that grew faster than any control nanowell would, while the rest of the well looks like control. Its unit
of inference is the **well**: nanowells of one well share its medium, seeding and handling and are not independent
replicates, so every p-value downstream is calibrated against the spread between replicate control wells.

This module is the first step and runs before any test:

* :func:`validate_table` checks the per-nanowell table against :data:`REQUIRED` (one row per nanowell: plate, well,
  condition, nanowell id, and either the **value** to test — the page's Metric at its Timepoint, in a ``value``
  column — or the initial count ``n0`` and final count ``nt``, whose fold change is then the value; optionally the
  two times, QC flags and the path of an image crop).
* :func:`prepare` takes the value (or computes the fold change ``fc = nt / n0``), the growth rate where counts are
  given, excludes nanowells with no value — or, for a count fold change, ``n0 = 0`` or no final count — and every
  nanowell a QC flag marks, logging each exclusion with its reason, and optionally scales each well robustly by its
  own bulk. **Nanowells are not stratified by default** (``strata_edges=None``): a nanowell's value is already
  measured against its own Baseline when the Metric is a fold change, which is what fixed starting-cell strata
  approximated, and fixed strata could be empty on a plate seeded outside them. Explicit ``strata_edges`` still
  group a count table by ``n0``.
* **Direction.** ``direction="lower"`` looks for responders in the bottom tail: the analysed value is the negated
  value, so every one-sided test downstream reads the bottom tail unchanged; ``Prepared.sign`` says which, and
  every figure and sentence states the original values.
* :func:`well_summary` is the per-well report: nanowells kept, the loading distribution, the median fold change.

**The per-well normalisation is off by default.** It divides out the well's bulk (median and MAD of the central
80 % of its fold changes), which removes a plate-position effect but also removes a real treatment effect on the
bulk. When on, the analysed value is a robust z-score rather than a fold change, and the figures say so.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

#: Columns every per-nanowell table carries; it also carries ``value``, or ``n0`` and ``nt`` (:func:`validate_table`).
REQUIRED = ("plate_id", "well_id", "condition", "nanowell_id")
#: The stratum every nanowell is in when the analysis is not stratified.
ALL = "all"
#: Columns it may carry. ``t0_h`` / ``t_h`` are the initial and final times in hours (the growth rate needs both);
#: ``crop_path`` names the nanowell's image crop for manual review. Any column whose name starts with
#: :data:`QC_PREFIX` is a QC flag (see :func:`qc_reasons`).
OPTIONAL = ("t0_h", "t_h", "crop_path")
QC_PREFIX = "qc_"
#: The 1.4826 that makes a MAD estimate a normal standard deviation.
MAD_SCALE = 1.4826
#: The share of a well's fold changes either side of its median that the normalisation reads: the central 80 %.
CENTRAL = (0.10, 0.90)

# Exclusion reasons, worded as the exclusion log writes them.
N0_ZERO = "N0 = 0 (no cell at the start)"
N0_MISSING = "N0 missing"
NT_MISSING = "final count missing"
BELOW_STRATA = "N0 below the lowest loading stratum"
NORMALISATION_UNDEFINED = "well cannot be normalised (MAD of its central 80% is 0)"
VALUE_MISSING = "no value of the metric at the Timepoint"


@dataclass(frozen=True)
class RareParams:
    """Every choice the analysis makes, fixed before it runs and written into its output (:meth:`as_log`)."""

    control: str = "control"                       # the condition name of the control wells
    percentile: float = 99.0                       # Test A's threshold: this percentile of control FC, per stratum
    sensitivity: tuple = (95.0, 99.0, 99.9)        # Test A is re-run at each, and every result shown
    fixed_threshold: float | None = None           # a threshold chosen before running, used instead of the percentile
    alpha0: float = 0.1                            # Test B: the HC+ maximum runs over i <= alpha0 * n
    strata_edges: tuple | None = None              # None: not stratified; (1, 2, 3, 4): n0 = 1, 2, 3, >= 4
    direction: str = "higher"                      # "higher": responders in the top tail; "lower": the bottom tail
    normalize: bool = False                        # per-well robust scaling (off: it removes a real bulk shift)
    min_wells_per_condition: int = 2               # fewer: no group-level inference, per-well results only
    min_controls_bb: int = 3                       # the beta-binomial regression needs this many control wells
    min_treated_bb: int = 2                        # ... and this many treatment wells
    by_plate: bool = False                         # True: each (plate, treatment) against that plate's controls
    max_permutations: int = 10000                  # labelings enumerated exhaustively up to this, sampled above
    seed: int = 12345                              # every random draw (permutations, validation) is seeded
    fdr: float = 0.05                              # Benjamini-Hochberg level, and the level "significant" means
    qc_columns: tuple | None = None                # None: every column starting with "qc_"
    validation_splits: int = 20                    # control-vs-control splits in the false-positive check
    validation_replicates: int = 20                # replicates per spike count in the power check
    spike_counts: tuple = (1, 3, 5, 10, 20)        # synthetic responders spiked per well in the power check
    spike_range: tuple = (2.0, 4.0)                # their FC: this multiple of the stratum's control 99th percentile
    validation_permutations: int = 1000            # the permutation cap inside each validation replicate

    def __post_init__(self) -> None:
        if not 0 < self.percentile < 100:
            raise ValueError(f"percentile must lie strictly between 0 and 100, not {self.percentile!r}")
        if any(not 0 < q < 100 for q in self.sensitivity):
            raise ValueError(f"every sensitivity percentile must lie strictly between 0 and 100: {self.sensitivity!r}")
        if not 0 < self.alpha0 <= 1:
            raise ValueError(f"alpha0 must lie in (0, 1], not {self.alpha0!r}")
        if self.strata_edges is not None:
            edges = tuple(self.strata_edges)
            if not edges or any(int(e) != e or e < 1 for e in edges) or list(edges) != sorted(set(edges)):
                raise ValueError(f"strata_edges must be increasing whole numbers >= 1, not {self.strata_edges!r}")
        if self.direction not in ("higher", "lower"):
            raise ValueError(f"direction must be 'higher' or 'lower', not {self.direction!r}")
        if self.min_wells_per_condition < 1:
            raise ValueError("min_wells_per_condition must be at least 1")

    def sensitivity_percentiles(self) -> tuple:
        """The sensitivity percentiles with the primary one among them, in increasing order."""
        return tuple(sorted(set(self.sensitivity) | {self.percentile}))

    def as_log(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}


def stratum_labels(edges) -> list[str]:
    """The strata's labels for *edges*: ``(1, 2, 3, 4)`` -> ``["1", "2", "3", "≥4"]``; a wider bin reads ``"2–4"``."""
    edges = [int(e) for e in edges]
    labels = []
    for lo, hi in zip(edges, edges[1:]):
        labels.append(str(lo) if hi == lo + 1 else f"{lo}–{hi - 1}")
    labels.append(f"≥{edges[-1]}")
    return labels


def assign_strata(n0: pd.Series, edges) -> pd.Series:
    """Each count's stratum label, or NA below the lowest edge."""
    edges = [int(e) for e in edges]
    labels = stratum_labels(edges)
    idx = np.searchsorted(np.asarray(edges, dtype=float), n0.to_numpy(dtype=float), side="right") - 1
    out = pd.Series(pd.NA, index=n0.index, dtype="object")
    ok = idx >= 0
    out[ok] = [labels[i] for i in idx[ok]]
    return out


def validate_table(table: pd.DataFrame) -> None:
    """Raise ``ValueError`` naming every required column *table* lacks: :data:`REQUIRED`, and ``value`` or both counts."""
    missing = [c for c in REQUIRED if c not in table.columns]
    if "value" not in table.columns:
        missing += [c for c in ("n0", "nt") if c not in table.columns]
    if missing:
        raise ValueError(f"the nanowell table lacks the required column(s): {', '.join(missing)}")


def qc_columns(table: pd.DataFrame, params: RareParams) -> list[str]:
    if params.qc_columns is not None:
        missing = [c for c in params.qc_columns if c not in table.columns]
        if missing:
            raise ValueError(f"QC column(s) named in the parameters are not in the table: {', '.join(missing)}")
        return list(params.qc_columns)
    return [c for c in table.columns if str(c).startswith(QC_PREFIX)]


def _flag_reason(column: str, values: pd.Series) -> pd.Series:
    """A QC column's verdict per row as a reason, or ``""`` for a pass.

    A boolean (or 0/1) column fails a row where it is true, with the column's name as the reason; a text column
    fails a row where it holds any text other than ``""`` / ``"ok"`` / ``"pass"``, the text being the reason."""
    name = column[len(QC_PREFIX):] if column.startswith(QC_PREFIX) else column
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        text = values.fillna("").astype(str).str.strip()
        failed = ~text.str.lower().isin(["", "ok", "pass", "false", "0", "nan"])
        return pd.Series(np.where(failed, "QC: " + name + " (" + text + ")", ""), index=values.index)
    failed = pd.to_numeric(values, errors="coerce").fillna(0).astype(float) != 0
    return pd.Series(np.where(failed, f"QC: {name}", ""), index=values.index)


def qc_reasons(table: pd.DataFrame, params: RareParams) -> pd.Series:
    """Every row's exclusion reasons, ``"; "``-joined (``""`` for a row that is kept)."""
    reasons = pd.Series("", index=table.index, dtype=object)

    def add(mask, text) -> None:
        mask = pd.Series(mask, index=table.index).fillna(False).astype(bool)
        reasons[mask] = np.where(reasons[mask] == "", text, reasons[mask] + "; " + text)

    if "value" in table.columns:
        add(pd.to_numeric(table["value"], errors="coerce").isna(), VALUE_MISSING)
    else:
        n0 = pd.to_numeric(table["n0"], errors="coerce")
        nt = pd.to_numeric(table["nt"], errors="coerce")
        add(n0.isna(), N0_MISSING)
        add(n0.eq(0), N0_ZERO)
        if params.strata_edges is not None:
            add(n0.gt(0) & n0.lt(min(params.strata_edges)), BELOW_STRATA)
        add(nt.isna(), NT_MISSING)
    for column in qc_columns(table, params):
        flag = _flag_reason(column, table[column])
        failed = flag != ""
        reasons[failed] = np.where(reasons[failed] == "", flag[failed], reasons[failed] + "; " + flag[failed])
    return reasons


@dataclass
class Prepared:
    """The kept nanowells, every exclusion with its reason, and the per-well summary."""

    kept: pd.DataFrame                      # one row per kept nanowell: well_key, plate_id, well_id, condition,
                                            # nanowell_id, n0, nt, fc, rate, stratum, value, crop_path
    exclusions: pd.DataFrame                # plate_id, well_id, condition, nanowell_id, reason
    wells: pd.DataFrame                     # well_summary(kept, exclusions)
    params: RareParams
    strata: list[str]
    value_label: str                        # what `value` is: the metric's label, "fold change", or its robust z
    notes: list[str] = field(default_factory=list)
    #: +1 when the analysed ``value`` is the value itself, -1 when it is negated (``direction="lower"``): the original
    #: value of a nanowell is ``sign * value``, and a cutoff ``t`` on the analysed value is ``sign * t`` originally.
    sign: int = 1


def well_key(plate, well) -> str:
    return f"{plate}:{well}"


def _rates(frame: pd.DataFrame) -> pd.Series:
    if "t0_h" not in frame.columns or "t_h" not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    dt = pd.to_numeric(frame["t_h"], errors="coerce") - pd.to_numeric(frame["t0_h"], errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = frame["nt"] / frame["n0"]
        rate = np.log2(ratio.where(ratio > 0)) / dt.where(dt > 0)
    return rate


def _normalise(kept: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    """Each kept nanowell's robust z within its own well; the keys of wells that cannot be normalised."""
    value = pd.Series(np.nan, index=kept.index)
    undefined = []
    for key, rows in kept.groupby("well_key", sort=False):
        fc = rows["fc"].to_numpy(dtype=float)
        lo, hi = np.quantile(fc, CENTRAL)
        central = fc[(fc >= lo) & (fc <= hi)]
        med = float(np.median(central))
        mad = float(np.median(np.abs(central - med))) * MAD_SCALE
        if not mad > 0:
            undefined.append(key)
            continue
        value[rows.index] = (fc - med) / mad
    return value, undefined


def prepare(table: pd.DataFrame, params: RareParams | None = None, *, value_label: str = "") -> Prepared:
    """QC, the value (or the count fold change), strata, direction and (optionally) normalisation of a per-nanowell
    *table*. *value_label* names a ``value`` column (the page's Metric label)."""
    params = params or RareParams()
    validate_table(table)
    frame = table.copy()
    given = "value" in frame.columns
    for column in ("n0", "nt"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce") if column in frame.columns else np.nan
    if given:
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    for column in ("plate_id", "well_id", "condition", "nanowell_id"):
        frame[column] = frame[column].astype(str)
    if "crop_path" not in frame.columns:
        frame["crop_path"] = ""
    frame["crop_path"] = frame["crop_path"].fillna("").astype(str)
    frame["well_key"] = [well_key(p, w) for p, w in zip(frame["plate_id"], frame["well_id"])]
    conflicting = frame.groupby("well_key")["condition"].nunique()
    if (conflicting > 1).any():
        raise ValueError("a well is recorded under more than one condition: "
                         + ", ".join(conflicting[conflicting > 1].index))
    reasons = qc_reasons(frame, params)
    exclusions = frame.loc[reasons != "", ["plate_id", "well_id", "condition", "nanowell_id"]].copy()
    exclusions["reason"] = reasons[reasons != ""]
    kept = frame.loc[reasons == ""].copy()
    # ``fc`` is the value as the user reads it — the metric, or the count fold change — whatever the direction.
    kept["fc"] = kept["value"] if given else kept["nt"] / kept["n0"]
    kept["rate"] = _rates(kept)
    kept["stratum"] = (assign_strata(kept["n0"], params.strata_edges) if params.strata_edges is not None and not given
                       else pd.Series(ALL, index=kept.index, dtype="object"))
    notes = []
    if params.normalize:
        kept["value"], undefined = _normalise(kept)
        if undefined:
            dropped = kept[kept["well_key"].isin(undefined)]
            extra = dropped[["plate_id", "well_id", "condition", "nanowell_id"]].copy()
            extra["reason"] = NORMALISATION_UNDEFINED
            exclusions = pd.concat([exclusions, extra], ignore_index=True)
            kept = kept[~kept["well_key"].isin(undefined)]
            notes.append(f"{len(undefined)} well(s) could not be normalised and were left out: {', '.join(undefined)}")
        value_label = f"robust z of {value_label or 'fold change'} (per-well normalised)"
    else:
        kept["value"] = kept["fc"]
        value_label = value_label or "fold change (Nt / N0)"
    sign = -1 if params.direction == "lower" else 1
    kept["value"] = sign * kept["value"]                    # the bottom tail, read as a top tail downstream
    columns = ["well_key", "plate_id", "well_id", "condition", "nanowell_id", "n0", "nt", "fc", "rate", "stratum",
               "value", "crop_path"]
    kept = kept[columns].reset_index(drop=True)
    exclusions = exclusions.reset_index(drop=True)
    labels = stratum_labels(params.strata_edges) if params.strata_edges is not None and not given else [ALL]
    strata = [s for s in labels if (kept["stratum"] == s).any()]
    return Prepared(kept, exclusions, well_summary(frame, kept, exclusions, params), params, strata, value_label,
                    notes, sign)


def well_summary(frame: pd.DataFrame, kept: pd.DataFrame, exclusions: pd.DataFrame,
                 params: RareParams) -> pd.DataFrame:
    """Per well: nanowells recorded, kept and excluded, the loading distribution of the kept, the median FC."""
    labels = stratum_labels(params.strata_edges) if params.strata_edges is not None else []
    rows = []
    for key, rows_all in frame.groupby("well_key", sort=True):
        mine = kept[kept["well_key"] == key]
        row = {"well_key": key, "plate_id": rows_all["plate_id"].iloc[0], "well_id": rows_all["well_id"].iloc[0],
               "condition": rows_all["condition"].iloc[0], "n_recorded": len(rows_all), "n_kept": len(mine),
               "n_excluded": len(rows_all) - len(mine),
               "median_value": float(mine["fc"].median()) if len(mine) else np.nan}
        for label in labels:
            row[f"n0_{label}"] = int((mine["stratum"] == label).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def exclusion_counts(exclusions: pd.DataFrame) -> dict:
    """How many nanowells each reason excluded (a nanowell with two reasons counts under each)."""
    counts: dict[str, int] = {}
    for text in exclusions.get("reason", pd.Series(dtype=object)):
        for reason in str(text).split("; "):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


#: The site table's per-row flags that become QC flags of a nanowell at its final timepoint (``nanowell_task``).
SITE_QC_FLAGS = {"fused": "qc_fused", "partially_visible": "qc_edge", "count_disagreement": "qc_count_disagreement"}


def from_site_metrics(metrics: pd.DataFrame, conditions: dict, final_timepoint: str, *, plate_id: str = "plate",
                      crop_path=None, value_column: str | None = None) -> pd.DataFrame:
    """A per-nanowell table for :func:`prepare` from the Nanowells page's metrics (``series_compute.site_metrics``).

    One row per nanowell of every well *conditions* names (``{well: condition}``): ``n0`` is the count at the
    metrics' Baseline, ``nt`` the count at *final_timepoint*. A nanowell that is not measurable at the final
    timepoint carries its state as ``qc_state``; a fused, partly visible or count-disagreeing site, or one whose
    identity check failed, carries that flag — so each is excluded with its reason rather than read as a count.
    *crop_path* is ``crop_path(well, q, r) -> str`` or ``None`` (no crop paths). *value_column* names the page's Metric
    column: its value at *final_timepoint* is the ``value`` the analysis tests (``None``: the count fold change)."""
    final = metrics[(metrics["timepoint"] == final_timepoint) & metrics["well"].isin(list(conditions))].copy()
    if final.empty:
        raise ValueError(f"no nanowell of the named wells has a row at timepoint {final_timepoint!r}")
    base_time = (metrics[metrics["timepoint"] == metrics["baseline_timepoint"]]
                 .set_index(["well", "nw_q", "nw_r"])["time_h"])
    keys = pd.MultiIndex.from_frame(final[["well", "nw_q", "nw_r"]])
    out = pd.DataFrame({
        "plate_id": plate_id, "well_id": final["well"].to_numpy(),
        "condition": final["well"].map(conditions).to_numpy(),
        "nanowell_id": [f"{int(q)},{int(r)}" for q, r in zip(final["nw_q"], final["nw_r"])],
        "n0": pd.to_numeric(final["n0"], errors="coerce").to_numpy(),
        "nt": pd.to_numeric(final["n"], errors="coerce").to_numpy(),
        "t0_h": base_time.reindex(keys).to_numpy(), "t_h": pd.to_numeric(final["time_h"], errors="coerce").to_numpy(),
    })
    if value_column is not None:
        if value_column not in final.columns:
            raise ValueError(f"the page's metrics have no column {value_column!r} to test")
        raw = final[value_column]
        if value_column == "occupancy_class":
            # "3+" is a value, not a missing one: read through the same map the Distribution and Extremes views use.
            from review.features.nanowell.views_compute import _OCCUPANCY_VALUE
            raw = raw.astype(str).map(_OCCUPANCY_VALUE)
        out["value"] = pd.to_numeric(raw, errors="coerce").to_numpy()
    state = final["state"].astype(str).to_numpy()
    out["qc_state"] = np.where(np.isin(state, ["measured", "empty"]), "", state)
    for column, flag in SITE_QC_FLAGS.items():
        if column in final.columns:
            out[flag] = final[column].eq(True).to_numpy()            # NA is not a flag
    if "identity_ok" in final.columns:
        out["qc_identity"] = (final["identity_ok"] != True).to_numpy()  # noqa: E712 — NA is not a pass
    out["crop_path"] = ([crop_path(w, int(q), int(r)) for w, q, r in zip(final["well"], final["nw_q"], final["nw_r"])]
                        if crop_path is not None else "")
    return out.reset_index(drop=True)
