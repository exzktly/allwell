"""Rare-responder analysis, the whole of it: run the three tests per treatment, correct, interpret, and report.

:func:`analyse` takes a per-nanowell table (``rare_data_compute.REQUIRED``) or an already prepared one and returns a
:class:`RareResult` holding everything the analysis decided and every number it computed:

* **Units.** One per treatment, its wells against every control well (``by_plate=False``, a plate term in the
  beta-binomial regression and permutations kept within each plate), or one per (plate, treatment) against that
  plate's controls (``by_plate=True``).
* **Guardrail.** A unit with fewer than ``min_wells_per_condition`` (2) wells on either side gets **no group-level
  inference** — no regression, no Fisher test, no permutation p — and says that replication is insufficient; its
  wells' per-well empirical results are still reported.
* **Per unit**: Test A (``rare_exceedance_compute``) at the primary percentile and at each sensitivity percentile,
  all of them shown, none chosen after the fact; Test B (``rare_hc_compute``) and Test C (``rare_ad_compute``),
  each with a per-well empirical p against the control wells' leave-one-out values and a whole-well permutation p.
* **Multiple testing**: Benjamini–Hochberg across every unit, separately for each test's group p-values.
* **Interpretation** (:func:`interpret`): enrichment of rare responders is supported when the exceedance test or HC
  is significant after correction; a significant AD alone is a general difference in the upper tail's shape.
* **Responders** (:func:`responders`): every nanowell above the primary threshold or at or below its well's HC
  threshold, with its crop path, for review by eye.
* **Parameter log**: every parameter, the strata, the exclusions by reason, the seed.

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from review.features.nanowell import rare_ad_compute as ad
from review.features.nanowell import rare_exceedance_compute as ex
from review.features.nanowell import rare_hc_compute as hc
from review.features.nanowell.rare_data_compute import Prepared, RareParams, exclusion_counts, prepare
from review.features.nanowell.rare_null_compute import (
    Permutation, Well, empirical_p, permutation_test, pool, wells_from,
)
from review.features.nanowell.rare_validate_compute import validate as run_validation
from review.features.nanowell.statistics_compute import bh_adjust

#: The verdict. **The exceedance test (Test A) alone decides it** — the question asked, in counts a reader can check;
#: Higher Criticism (B) and the upper-tail shape test (C) are reported beside it as supporting evidence and never
#: change it (``rare_export_compute.supporting_line``), so two tests are never given two chances at one verdict.
DETECTED = "rare responders detected"
NOT_DETECTED = "rare responders not detected"
INSUFFICIENT = "too few wells to test the group"


@dataclass
class Unit:
    """One treatment (on one plate, or on all) against its control wells, with every result computed for it."""

    plate: str
    treatment: str
    controls: list
    treated: list
    group_allowed: bool
    bb_allowed: bool
    refusal: str = ""
    exceedance: dict = field(default_factory=dict)       # percentile -> ExceedanceResult
    hc: dict = field(default_factory=dict)               # well key -> HC (controls leave-one-out)
    hc_p: dict = field(default_factory=dict)             # treatment well key -> empirical p
    hc_perm: Permutation | None = None
    a2: dict = field(default_factory=dict)               # well key -> (A²_R, {stratum: value})
    a2_p: dict = field(default_factory=dict)
    a2_perm: Permutation | None = None
    scholz: tuple = (math.nan, math.nan, "")
    resolution: str = ""
    q: dict = field(default_factory=dict)                # "exceedance" / "hc" / "ad" -> BH q
    interpretation: str = ""
    thresholds: dict = field(default_factory=dict)       # stratum -> primary threshold (from all control wells)
    #: +1, or -1 when the analysed values are negated to read the bottom tail (``Prepared.sign``): a figure or sentence
    #: shows ``sign * value`` and ``sign * threshold``.
    sign: int = 1

    @property
    def label(self) -> str:
        return self.treatment if self.plate == "all" else f"{self.treatment} @ {self.plate}"

    @property
    def primary(self) -> ex.ExceedanceResult:
        return self.exceedance[min(self.exceedance, key=lambda q: abs(q - self._primary_q))]

    _primary_q: float = 99.0


@dataclass
class RareResult:
    prepared: Prepared
    units: list
    table: pd.DataFrame          # one row per unit
    sensitivity: pd.DataFrame    # one row per unit and percentile
    per_well: pd.DataFrame       # one row per well and unit
    responders: pd.DataFrame     # flagged nanowells, with crop paths
    log: dict
    warnings: list = field(default_factory=list)
    validation: object = None    # rare_validate_compute.Validation, when run
    page_notes: list = field(default_factory=list)   # what the Nanowells page adds under the report (its Details)


def _units(wells: dict[str, Well], params: RareParams) -> list[tuple[str, str, list, list]]:
    conditions = sorted({w.condition for w in wells.values()})
    if params.control not in conditions:
        raise ValueError(f"no well is labelled with the control condition {params.control!r}; conditions found: "
                         + ", ".join(conditions))
    treatments = [c for c in conditions if c != params.control]
    out = []
    plates = sorted({w.plate for w in wells.values()}) if params.by_plate else ["all"]
    for plate in plates:
        here = [w for w in wells.values() if plate == "all" or w.plate == plate]
        controls = [w for w in here if w.condition == params.control]
        for t in treatments:
            treated = [w for w in here if w.condition == t]
            if treated:
                out.append((plate, t, controls, treated))
    return out


def _run_unit(plate, treatment, controls, treated, params: RareParams) -> Unit:
    n_c, n_t = len(controls), len(treated)
    group = n_c >= params.min_wells_per_condition and n_t >= params.min_wells_per_condition
    bb = group and n_c >= params.min_controls_bb and n_t >= params.min_treated_bb
    unit = Unit(plate, treatment, controls, treated, group, bb)
    unit._primary_q = params.percentile
    if not group:
        unit.refusal = (f"{n_c} control and {n_t} treatment well(s); group-level inference needs at least "
                        f"{params.min_wells_per_condition} of each")
    full = pool(controls)
    unit.thresholds = ex.threshold_of(full, params.percentile, params.fixed_threshold)
    percentiles = [params.percentile] if params.fixed_threshold is not None else params.sensitivity_percentiles()
    for q in percentiles:
        unit.exceedance[q] = ex.exceedance_test(controls, treated, q, params.fixed_threshold,
                                                bb_allowed=bb, group_allowed=group)
    _run_hc(unit, controls, treated, full, params)
    _run_ad(unit, controls, treated, full, params)
    return unit


def _permute(unit: Unit, statistic, params: RareParams) -> Permutation | None:
    if not unit.group_allowed:
        return None
    units = unit.controls + unit.treated
    return permutation_test(units, {w.key for w in unit.treated}, statistic, params.max_permutations, params.seed)


def _run_hc(unit: Unit, controls, treated, full, params: RareParams) -> None:
    for w in controls:
        unit.hc[w.key] = hc.higher_criticism(w, pool([o for o in controls if o.key != w.key]), params.alpha0)
    null = [unit.hc[w.key].value for w in controls]
    for w in treated:
        unit.hc[w.key] = hc.higher_criticism(w, full, params.alpha0)
        unit.hc_p[w.key] = empirical_p(unit.hc[w.key].value, null)
    unit.hc_perm = _permute(unit, hc.statistic(params.alpha0), params)
    n = max((w.n for w in treated), default=0)
    unit.resolution = hc.resolution_warning(len(controls), full, n)


def _run_ad(unit: Unit, controls, treated, full, params: RareParams) -> None:
    for w in controls:
        unit.a2[w.key] = ad.a2r(w, pool([o for o in controls if o.key != w.key]))
    null = [unit.a2[w.key][0] for w in controls]
    for w in treated:
        unit.a2[w.key] = ad.a2r(w, full)
        unit.a2_p[w.key] = empirical_p(unit.a2[w.key][0], null)
    unit.a2_perm = _permute(unit, ad.statistic, params)
    t_vals = np.concatenate([np.concatenate(list(w.values.values())) for w in treated]) if treated else np.array([])
    c_vals = np.concatenate(list(full.values())) if full else np.array([])
    unit.scholz = ad.scholz_stephens(t_vals, c_vals)


def _p(perm: Permutation | None) -> float:
    return perm.p if perm is not None else math.nan


def _bh(units: list[Unit], key: str, ps: list[float]) -> None:
    raw = [p if np.isfinite(p) else None for p in ps]
    adjusted, _corrected = bh_adjust(raw)
    for unit, q in zip(units, adjusted):
        unit.q[key] = q if q is not None else math.nan


def significant(unit: Unit, test: str, fdr: float) -> bool:
    """Whether *test* (``"exceedance"``, ``"hc"`` or ``"ad"``) is significant for *unit*: its q below *fdr*."""
    q = unit.q.get(test, math.nan)
    return bool(np.isfinite(q) and q < fdr)


def interpret(unit: Unit, fdr: float) -> str:
    """The verdict, decided by the exceedance test alone (:data:`DETECTED`). What the design cannot show — a
    permutation floor above *fdr*, a regression that could not estimate the spread between wells — is said beside
    the verdict (``rare_export_compute.caveats``), not folded into it."""
    if not unit.group_allowed:
        return INSUFFICIENT
    return DETECTED if significant(unit, "exceedance", fdr) else NOT_DETECTED


def _finite_mean(values) -> float:
    """The mean of the finite *values*; NaN — not computed — when there are none (no "mean of empty slice")."""
    finite = [float(v) for v in values if np.isfinite(v)]
    return float(np.mean(finite)) if finite else math.nan


def _row(unit: Unit) -> dict:
    a = unit.primary
    mean_hc = _finite_mean([unit.hc[w.key].value for w in unit.treated])
    mean_a2 = _finite_mean([unit.a2[w.key][0] for w in unit.treated])
    return {
        "treatment": unit.treatment, "plate": unit.plate, "n_treatment_wells": len(unit.treated),
        "n_control_wells": len(unit.controls), "group_inference": unit.group_allowed, "refusal": unit.refusal,
        "exceedance_method": a.method, "percentile": a.percentile, "exceedance_or": a.odds_ratio,
        "exceedance_or_ci_low": a.ci[0], "exceedance_or_ci_high": a.ci[1], "rho": a.rho, "exceedance_p": a.p,
        "exceedance_q": unit.q.get("exceedance", math.nan), "exceedance_min_p_per_well": a.min_p,
        "fisher_p_anticonservative": a.fisher_p, "fisher_or": a.fisher_or,
        "hc_mean": mean_hc, "hc_p": _p(unit.hc_perm), "hc_q": unit.q.get("hc", math.nan),
        "hc_permutations": unit.hc_perm.n_labelings if unit.hc_perm else 0,
        "hc_exhaustive": unit.hc_perm.exhaustive if unit.hc_perm else False,
        "hc_min_p": unit.hc_perm.min_p if unit.hc_perm else math.nan,
        "a2r_mean": mean_a2, "a2r_p": _p(unit.a2_perm), "a2r_q": unit.q.get("ad", math.nan),
        "a2r_permutations": unit.a2_perm.n_labelings if unit.a2_perm else 0,
        "a2r_min_p": unit.a2_perm.min_p if unit.a2_perm else math.nan,
        "scholz_stephens_stat_uncalibrated": unit.scholz[0], "scholz_stephens_p_uncalibrated": unit.scholz[1],
        "n_control_wells_in_null": len(unit.controls), "interpretation": unit.interpretation,
        "notes": "; ".join(a.notes + ([unit.resolution] if unit.resolution else [])),
    }


def _sensitivity(units: list[Unit]) -> pd.DataFrame:
    rows = []
    for unit in units:
        for q, a in sorted(unit.exceedance.items()):
            rows.append({"treatment": unit.treatment, "plate": unit.plate, "percentile": q, "method": a.method,
                         "odds_ratio": a.odds_ratio, "ci_low": a.ci[0], "ci_high": a.ci[1], "rho": a.rho, "p": a.p,
                         "fisher_p_anticonservative": a.fisher_p,
                         "k_treatment": sum(c.k for c in a.counts if c.treated),
                         "n_treatment": sum(c.n for c in a.counts if c.treated),
                         "k_control": sum(c.k for c in a.counts if not c.treated),
                         "n_control": sum(c.n for c in a.counts if not c.treated)})
    return pd.DataFrame(rows)


def _per_well(units: list[Unit]) -> pd.DataFrame:
    rows = []
    for unit in units:
        counts = {c.key: c for c in unit.primary.counts}
        for w in unit.controls + unit.treated:
            treated = w in unit.treated
            h = unit.hc[w.key]
            c = counts[w.key]
            rows.append({"unit": unit.label, "well_key": w.key, "plate_id": w.plate, "condition": w.condition,
                         "role": "treatment" if treated else "control (leave-one-out)", "n": w.n,
                         "k_exceed": c.k, "exceed_rate": c.rate,
                         "exceed_empirical_p": c.empirical_p if treated else math.nan,
                         "hc": h.value, "hc_i_star": h.i_star, "hc_p_threshold": h.p_star,
                         "hc_candidates": len(h.candidates), "hc_empirical_p": unit.hc_p.get(w.key, math.nan),
                         "a2r": unit.a2[w.key][0], "a2r_empirical_p": unit.a2_p.get(w.key, math.nan),
                         **{f"a2r_stratum_{s}": v for s, v in unit.a2[w.key][1].items()}})
    return pd.DataFrame(rows)


def responders(units: list[Unit], prepared: Prepared) -> pd.DataFrame:
    """Every treatment-well nanowell flagged for review: **above the control cutoff** (the primary threshold of its
    stratum), the deciding test's own criterion. ``hc_candidate`` says whether it is also at or below its well's
    Higher Criticism threshold. HC's candidates alone are not flagged: HC always has a peak, so on a plate with no
    responders every well still has candidates (15 on the synthetic null plate), and flagging them read as a
    finding beside a verdict of none."""
    kept = prepared.kept.set_index(["well_key", "nanowell_id"])
    rows = []
    for unit in units:
        thr = unit.thresholds
        for w in unit.treated:
            hc_ids = {nid: p for nid, _s, p in unit.hc[w.key].candidates}
            for s, values in w.values.items():
                for nid, v in zip(w.ids[s], values):
                    above = s in thr and v > thr[s]
                    if not above:
                        continue
                    src = kept.loc[(w.key, nid)]
                    rows.append({"unit": unit.label, "well_key": w.key, "plate_id": w.plate,
                                 "well_id": src["well_id"], "condition": w.condition, "nanowell_id": nid,
                                 "stratum": s, "n0": src["n0"], "nt": src["nt"], "fc": src["fc"], "value": v,
                                 "threshold": thr.get(s, math.nan), "above_threshold": above,
                                 "hc_candidate": nid in hc_ids, "hc_p": hc_ids.get(nid, math.nan),
                                 "crop_path": src["crop_path"]})
    return pd.DataFrame(rows)


def parameter_log(prepared: Prepared, units: list[Unit]) -> dict:
    return {"parameters": prepared.params.as_log(), "strata_present": prepared.strata,
            "value": prepared.value_label, "nanowells_kept": int(len(prepared.kept)),
            "nanowells_excluded": int(len(prepared.exclusions)),
            "exclusions_by_reason": exclusion_counts(prepared.exclusions),
            "units": [{"treatment": u.treatment, "plate": u.plate, "control_wells": [w.key for w in u.controls],
                       "treatment_wells": [w.key for w in u.treated],
                       "thresholds": {s: float(v) for s, v in u.thresholds.items()}} for u in units],
            "notes": list(prepared.notes)}


def analyse(data, params: RareParams | None = None, *, validate: bool = False,
            value_label: str = "") -> RareResult:
    """Run the rare-responder analysis on a per-nanowell table (or a ``Prepared`` one); see the module docstring."""
    prepared = data if isinstance(data, Prepared) else prepare(data, params, value_label=value_label)
    params = prepared.params
    wells = wells_from(prepared.kept)
    units = [_run_unit(*spec, params) for spec in _units(wells, params)]
    for u in units:
        u.sign = prepared.sign
    _bh(units, "exceedance", [u.primary.p for u in units])
    _bh(units, "hc", [_p(u.hc_perm) for u in units])
    _bh(units, "ad", [_p(u.a2_perm) for u in units])
    for u in units:
        u.interpretation = interpret(u, params.fdr)
    warnings = [f"{u.label}: {u.refusal}" for u in units if u.refusal]
    warnings += [f"{u.label}: {u.resolution}" for u in units if u.resolution]
    result = RareResult(prepared, units, pd.DataFrame([_row(u) for u in units]), _sensitivity(units),
                        _per_well(units), responders(units, prepared), parameter_log(prepared, units), warnings)
    if validate:
        result.validation = run_validation(prepared)
        result.log["validation"] = result.validation.log
    return result
