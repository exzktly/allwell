"""The rare-responder analysis (``review/features/nanowell/rare_*_compute.py``): QC and strata, the three tests, their
calibration against whole wells, the guardrails, the report, the self-check and the figures.

Fixtures are built so the right and the wrong implementation give different numbers (CLAUDE.md §7.8): thresholds
that differ by stratum, a control well that would lower its own bar, p-values hand-computed for the HC sum, plates
whose within-plate permutations are fewer than the pooled ones.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import rare_ad_compute as ad
from review.features.nanowell import rare_exceedance_compute as ex
from review.features.nanowell import rare_hc_compute as hc
from review.features.nanowell import rare_report_compute as rr
from review.features.nanowell.rare_data_compute import (
    N0_ZERO, NORMALISATION_UNDEFINED, RareParams, from_site_metrics, prepare, stratum_labels,
)
from review.features.nanowell.rare_null_compute import Well, empirical_p, labelings, permutation_test, wells_from


def plate(n_ctrl=6, n_treat=3, n=400, responders=0, seed=0, well_sd=0.15, treatment="drug", plate_id="P1"):
    """A synthetic plate: log-normal growth around a per-well shift; *responders* nanowells per treatment well grow
    2^6.5-fold, far beyond any control."""
    rng = np.random.default_rng(seed)
    rows = []
    for cond, n_wells in (("control", n_ctrl), (treatment, n_treat)):
        for w in range(n_wells):
            mu = rng.normal(0, well_sd)
            n0 = rng.choice([1, 2, 3, 4, 5], size=n, p=[.35, .3, .15, .1, .1])
            nt = rng.poisson(n0 * 2 ** (2 + mu + rng.normal(0, 0.5, n)))
            if cond != "control" and responders:
                idx = rng.choice(n, responders, replace=False)
                nt[idx] = (n0[idx] * 2 ** 6.5).astype(int)
            for i in range(n):
                rows.append({"plate_id": plate_id, "well_id": f"{'co' if cond == 'control' else cond}{w}",
                             "condition": cond,
                             "nanowell_id": str(i), "n0": n0[i], "nt": nt[i], "t0_h": 0.0, "t_h": 72.0,
                             "crop_path": f"crops/{cond}{w}_{i}.png"})
    return pd.DataFrame(rows)


def _well(key, values, condition="control", plate_id="P1", stratum="1"):
    v = np.sort(np.asarray(values, dtype=float))
    return Well(key, plate_id, condition, {stratum: v}, {stratum: np.array([f"{key}-{i}" for i in range(len(v))],
                                                                           dtype=object)})


# ── QC, strata, fold change ─────────────────────────────────────────────────────────────────────────────────────
def test_exclusions_are_logged_with_their_reasons_and_strata_follow_n0():
    t = pd.DataFrame({"plate_id": "P", "well_id": "A1", "condition": "control", "nanowell_id": list("abcdef"),
                      "n0": [0, 1, 2, 3, 7, 2], "nt": [5, 4, 8, 3, 14, np.nan], "t0_h": 0, "t_h": 48,
                      "qc_debris": [False, False, True, False, False, False],
                      "qc_focus": ["", "", "", "", "", "blurred"]})
    p = prepare(t, RareParams(strata_edges=(1, 2, 3, 4)))      # strata only when asked for
    reasons = dict(zip(p.exclusions["nanowell_id"], p.exclusions["reason"]))
    assert reasons == {"a": N0_ZERO, "c": "QC: debris", "f": "final count missing; QC: focus (blurred)"}
    assert list(p.kept["nanowell_id"]) == ["b", "d", "e"]
    assert list(p.kept["stratum"]) == ["1", "3", "≥4"]
    assert list(p.kept["fc"]) == [4.0, 1.0, 2.0]
    assert p.kept["rate"].tolist() == pytest.approx([2 / 48, 0.0, 1 / 48])
    row = p.wells.iloc[0]
    assert (row["n_recorded"], row["n_kept"], row["n_excluded"], row["median_value"]) == (6, 3, 3, 2.0)
    assert (row["n0_1"], row["n0_2"], row["n0_3"], row["n0_≥4"]) == (1, 0, 1, 1)


def test_a_final_count_of_zero_has_fold_change_zero_and_no_growth_rate():
    t = pd.DataFrame({"plate_id": "P", "well_id": "A1", "condition": "control", "nanowell_id": ["a"],
                      "n0": [2], "nt": [0], "t0_h": 0, "t_h": 24})
    kept = prepare(t).kept
    assert kept["fc"].iloc[0] == 0.0 and math.isnan(kept["rate"].iloc[0])


def test_stratum_labels_name_wide_bins():
    assert stratum_labels((1, 2, 3, 4)) == ["1", "2", "3", "≥4"]
    assert stratum_labels((1, 2, 5)) == ["1", "2–4", "≥5"]


def test_normalisation_is_off_by_default_and_a_flat_well_is_excluded_when_on():
    t = plate(n_ctrl=2, n_treat=0, n=50)
    assert (prepare(t).kept["value"] == prepare(t).kept["fc"]).all()
    flat = t.copy()
    flat.loc[flat["well_id"] == "co0", "nt"] = flat.loc[flat["well_id"] == "co0", "n0"] * 2
    p = prepare(flat, RareParams(normalize=True))
    assert set(p.kept["well_key"]) == {"P1:co1"}
    assert (p.exclusions["reason"] == NORMALISATION_UNDEFINED).sum() == 50
    z = p.kept["value"]
    assert abs(float(np.median(z))) < 0.2 and "robust z" in p.value_label


# ── Test A ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_control_well_is_counted_against_the_other_control_wells_only():
    a = _well("A", np.r_[np.ones(99), 100.0])
    b = _well("B", np.arange(1, 101))
    c = _well("C", np.arange(1, 101) * 2.0)
    counts = {wc.key: wc for wc in ex.well_counts([a, b, c], [], 99.0, None)}
    expected = float(np.quantile(np.r_[np.arange(1, 101), np.arange(1, 101) * 2.0], 0.99))
    assert counts["A"].thresholds == {"1": expected}
    # Against a pool including its own nanowells the bar would be the same pool's 99th percentile with A's 100.0
    # in it, and A's count would be the same 1 — so check B, whose own top values would raise its bar.
    own = float(np.quantile(np.r_[a.values["1"], b.values["1"], c.values["1"]], 0.99))
    assert counts["B"].thresholds["1"] != own
    assert counts["B"].k == int(np.sum(b.values["1"] > counts["B"].thresholds["1"]))


def test_thresholds_are_per_stratum():
    ctrl = [Well(k, "P", "control", {"1": np.linspace(0, 10, 200), "≥4": np.linspace(0, 2, 200)},
                 {"1": np.arange(200), "≥4": np.arange(200)}) for k in ("A", "B")]
    treat = Well("T", "P", "drug", {"1": np.array([5.0, 9.99, 11.0]), "≥4": np.array([1.0, 1.995, 2.5])},
                 {"1": np.arange(3), "≥4": np.arange(3)})
    wc = [c for c in ex.well_counts(ctrl, [treat], 99.0, None) if c.treated][0]
    # Per stratum: 11.0 above stratum 1's ~9.9 and 2.5 above stratum ≥4's ~1.98, and 9.99 and 1.995 too. A pooled
    # threshold (~9.8) would count only 9.99 and 11.0.
    assert wc.per_stratum == {"1": (2, 3), "≥4": (2, 3)} and (wc.k, wc.n) == (4, 6)


def test_a_fixed_threshold_replaces_the_percentile():
    ctrl = [_well("A", np.arange(10)), _well("B", np.arange(10))]
    wc = ex.well_counts(ctrl, [_well("T", [3, 4, 5, 6], "drug")], 99.0, 4.5)
    assert {c.key: c.k for c in wc} == {"A": 5, "B": 5, "T": 2}


def test_beta_binomial_fit_recovers_overdispersion():
    rng = np.random.default_rng(4)
    n = np.full(300, 500)
    p = rng.beta(0.02 * (1 - 0.05) / 0.05, 0.98 * (1 - 0.05) / 0.05, size=300)
    k = rng.binomial(n, p)
    fit = ex.fit_bb(k, n, np.ones((300, 1)))
    assert fit.rho == pytest.approx(0.05, rel=0.35)
    assert ex.fit_bb(rng.binomial(n, 0.02), n, np.ones((300, 1))).rho < 0.005


def test_the_exceedance_test_is_one_sided_and_reports_odds_ratio_with_interval():
    up = rr.analyse(plate(responders=40, seed=1))
    a = up.units[0].primary
    assert a.method == ex.BB_LABEL and a.p < 0.01 and a.odds_ratio > 1
    assert a.ci[0] < a.odds_ratio < a.ci[1]
    t = plate(seed=1)
    t.loc[t["condition"] == "drug", "nt"] = t.loc[t["condition"] == "drug", "n0"]       # treatment grows less
    down = rr.analyse(t).units[0].primary
    assert down.p > 0.5


def test_the_exceedance_test_holds_its_size_between_wells_that_differ():
    """Twenty null plates with a real well-to-well shift: a nanowell-level test would reject far more often."""
    ps = []
    for s in range(20):
        wells = wells_from(prepare(plate(n=300, seed=500 + s, well_sd=0.3)).kept)
        c = [w for w in wells.values() if w.condition == "control"]
        t = [w for w in wells.values() if w.condition != "control"]
        ps.append(ex.exceedance_test(c, t, 99.0, None, bb_allowed=True, group_allowed=True, ci=False).p)
    assert np.mean(np.array(ps) < 0.05) <= 0.15


def test_too_few_wells_falls_back_to_the_empirical_null_with_fisher_labelled():
    res = rr.analyse(plate(n_ctrl=2, n_treat=2, responders=30))
    unit = res.units[0]
    a = unit.primary
    assert unit.group_allowed and not unit.bb_allowed
    assert a.method == ex.EMPIRICAL_LABEL and math.isnan(a.p) and a.fisher_p < 0.05
    assert all(math.isfinite(c.empirical_p) for c in a.counts if c.treated)
    assert ex.FISHER_LABEL.endswith("anti-conservative")
    # Fisher is significant, but no interpretation rests on it.
    assert not np.isfinite(unit.q["exceedance"])
    assert "two control wells" in unit.resolution


def test_one_treatment_well_refuses_group_inference():
    res = rr.analyse(plate(n_ctrl=5, n_treat=1, responders=30))
    unit = res.units[0]
    assert not unit.group_allowed and unit.interpretation == rr.INSUFFICIENT
    assert unit.hc_perm is None and unit.a2_perm is None and math.isnan(unit.primary.fisher_p)
    assert math.isfinite(unit.hc_p["P1:drug0"]) and math.isfinite(unit.primary.counts[-1].empirical_p)
    assert any("group-level inference needs at least 2" in w for w in res.warnings)


def test_a_plate_covariate_at_its_bound_does_not_make_the_treatment_odds_ratio_unbounded():
    """A plate on which no well exceeds the threshold drives its own plate coefficient to the bound; the treatment
    coefficient is finite and estimable from the other plate. The fit reported "an estimate reached its bound" for
    any coefficient, so the odds ratio was printed as ∞ beside a finite interval. Only the treatment coefficient
    reaching its bound makes the odds ratio unbounded."""
    rows = [ex.WellCount(f"A:c{i}", "A", "control", False, k, 20_000, {}, {}) for i, k in enumerate([3, 2, 4])]
    rows += [ex.WellCount(f"A:t{i}", "A", "drug", True, k, 20_000, {}, {}) for i, k in enumerate([6, 5])]
    rows += [ex.WellCount(f"B:c{i}", "B", "control", False, 0, 20_000, {}, {}) for i in range(3)]
    rows += [ex.WellCount(f"B:t{i}", "B", "drug", True, 0, 20_000, {}, {}) for i in range(2)]
    r = ex.beta_binomial_test(rows, 99.0)
    assert math.isfinite(r.odds_ratio)
    assert r.odds_ratio == pytest.approx(math.exp(r.beta1))
    assert 1.0 < r.odds_ratio < 4.0 and r.ci[0] < r.odds_ratio < r.ci[1]
    assert not any("complete separation" in note for note in r.notes)


def _null_plate(n_ctrl, n_treat, n=300, seed=11):
    """Control and treatment wells drawn from one distribution: nothing in it is a rare responder."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_ctrl + n_treat):
        n0 = rng.integers(1, 6, n)
        nt = rng.poisson(n0 * np.exp(rng.normal(1.5, 0.5, n)))
        rows += [("p", f"W{i}", "control" if i < n_ctrl else "drug", f"{j},0", n0[j], nt[j]) for j in range(n)]
    return pd.DataFrame(rows, columns=["plate_id", "well_id", "condition", "nanowell_id", "n0", "nt"])


def test_on_a_plate_with_no_responders_hc_candidates_alone_are_never_flagged():
    """Higher Criticism always has a peak, so every null well has candidates. The flagged list is the nanowells past
    the control cutoff and nothing else: a candidate below the cutoff is never put in front of the reviewer as a
    finding beside a verdict of none."""
    res = rr.analyse(_null_plate(4, 4))
    assert res.units[0].interpretation == rr.NOT_DETECTED
    flagged = res.responders
    assert (flagged["above_threshold"]).all()
    assert len(flagged) < sum(len(res.units[0].hc[w.key].candidates) for w in res.units[0].treated)


def test_one_control_well_gives_no_calibrated_result_and_does_not_promise_one():
    """With one control well its leave-one-out pool is empty, so the control null is empty and every treatment
    well's empirical p is undefined. The verdict once said "per-well empirical results only" over a table of NaN — a
    result promised and not computed (CLAUDE.md §7 #3). It says the group cannot be tested and promises nothing per
    well; with no control well at all, the resolution warning no longer calls it "a single control well (0
    nanowells)"."""
    res = rr.analyse(_null_plate(1, 3))
    unit = res.units[0]
    treated = res.per_well[res.per_well["role"] == "treatment"]
    for column in ("exceed_empirical_p", "hc_empirical_p", "a2r_empirical_p"):
        assert treated[column].isna().all(), column       # nothing per-well was computed
    assert "per-well" not in unit.interpretation
    assert unit.interpretation == rr.INSUFFICIENT
    from review.features.nanowell import rare_hc_compute as hcc
    assert "single control well" not in hcc.resolution_warning(0, {}, 300)
    assert "no control well" in hcc.resolution_warning(0, {}, 300)


def test_every_sensitivity_percentile_is_reported():
    res = rr.analyse(plate(responders=10), RareParams(percentile=99.0))
    assert sorted(res.sensitivity["percentile"]) == [95.0, 99.0, 99.9]
    assert res.table["percentile"].iloc[0] == 99.0


# ── Test B ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_p_values_count_ties_in_the_control_pool():
    w = _well("T", [1.0, 5.0, 11.0], "drug")
    p, _ids, _s, unscored = hc.p_values(w, {"1": np.arange(1.0, 11.0)})
    assert p.tolist() == pytest.approx([11 / 11, 7 / 11, 1 / 11]) and unscored == 0


def test_hc_plus_is_the_hand_computed_maximum_and_skips_p_at_or_below_1_over_n():
    p = np.r_[0.0005, 0.004, 0.01, np.linspace(0.05, 1.0, 97)]
    n = 100
    value, i_star, p_star, _x, _y, _ = hc.hc_plus(p, 0.1)
    ps = np.sort(p)
    expect = [(math.sqrt(n) * (i / n - ps[i - 1]) / math.sqrt(ps[i - 1] * (1 - ps[i - 1])), i)
              for i in range(1, 11) if ps[i - 1] > 1 / n and ps[i - 1] < 1]
    best = max(expect)
    assert (value, i_star) == pytest.approx(best) and p_star == ps[best[1] - 1]
    assert all(i != 1 for _v, i in expect)               # 0.0005 <= 1/n: HC+ leaves it out


def test_hc_candidates_are_the_nanowells_at_or_below_the_threshold():
    ctrl = np.linspace(0, 1, 2000)
    w = _well("T", np.r_[np.linspace(0, 1, 95), [5, 6, 7, 8, 9]], "drug")
    h = hc.higher_criticism(w, {"1": ctrl}, 0.1)
    assert {c[0] for c in h.candidates} >= {"T-95", "T-96", "T-97", "T-98", "T-99"}
    assert all(c[2] <= h.p_star for c in h.candidates)


def test_hc_detects_sparse_responders_that_a_mean_shift_test_would_not():
    res = rr.analyse(plate(responders=8, seed=2))
    unit = res.units[0]
    assert unit.hc_perm.p == pytest.approx(unit.hc_perm.min_p) and unit.hc_perm.exhaustive
    assert unit.hc_perm.n_labelings == math.comb(9, 3)
    assert unit.interpretation == rr.DETECTED           # decided by the exceedance test; HC reported beside it


# ── permutations ────────────────────────────────────────────────────────────────────────────────────────────────
def test_permutations_keep_each_plate_s_treatment_count():
    wells = [_well(f"{p}{i}", [i], plate_id=p) for p in ("P1", "P2") for i in range(4)]
    labs, exhaustive = labelings(wells, {"P10", "P11", "P20"}, 10_000, 0)
    assert exhaustive and len(labs) == math.comb(4, 2) * math.comb(4, 1) == 24     # not comb(8, 3) = 56
    assert labs[0] == frozenset({"P10", "P11", "P20"}) and len(set(labs)) == 24


def test_sampled_permutations_are_distinct_and_seeded():
    wells = [_well(f"W{i}", [i]) for i in range(20)]
    a, ex_a = labelings(wells, {"W0", "W1", "W2", "W3"}, 50, 7)
    b, _ = labelings(wells, {"W0", "W1", "W2", "W3"}, 50, 7)
    assert not ex_a and a == b and len(set(a)) == 50


def test_permutation_p_counts_the_observed_labeling():
    wells = [_well(f"C{i}", np.arange(50.0) + i) for i in range(4)] + [
        _well(f"T{i}", np.arange(50.0) + 100, "drug") for i in range(2)]
    perm = permutation_test(wells, {"T0", "T1"}, ad.statistic, 10_000, 0)
    assert perm.n_labelings == 15 and perm.p == pytest.approx(1 / 15) and perm.min_p == pytest.approx(1 / 15)


def test_empirical_p_includes_the_observation():
    assert empirical_p(3.0, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(3 / 5)
    assert math.isnan(empirical_p(math.nan, [1.0]))


# ── Test C ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a2r_counts_only_an_upper_tail_excess():
    ref = np.linspace(0, 1, 1001)
    assert ad.a2r_stratum(np.linspace(0, 1, 300), ref) == pytest.approx(0.0, abs=0.02)
    assert ad.a2r_stratum(np.linspace(0, 0.9, 300), ref) == 0.0                   # a deficit counts nothing
    lower = np.r_[np.linspace(0, 0.2, 150), np.linspace(0.5, 1, 150)]           # differs only below the median
    assert ad.a2r_stratum(lower, ref) == pytest.approx(0.0, abs=0.05)
    assert ad.a2r_stratum(np.r_[np.linspace(0, 1, 280), np.full(20, 0.999)], ref) > 1.0


def test_a2r_combines_strata_by_nanowell_share():
    ref = {"1": np.linspace(0, 1, 1001), "2": np.linspace(0, 1, 1001)}
    w = Well("T", "P", "drug", {"1": np.sort(np.r_[np.linspace(0, 1, 90), np.full(10, 0.999)]),
                                "2": np.linspace(0, 1, 300)}, {"1": np.arange(100), "2": np.arange(300)})
    total, per = ad.a2r(w, ref)
    assert total == pytest.approx(0.25 * per["1"] + 0.75 * per["2"])


def test_scholz_stephens_is_reported_as_uncalibrated():
    res = rr.analyse(plate(responders=10))
    assert "uncalibrated" in ad.SCHOLZ_LABEL and "i.i.d." in ad.SCHOLZ_LABEL
    assert math.isfinite(res.table["scholz_stephens_p_uncalibrated"].iloc[0])


# ── reporting ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_bh_runs_across_treatments_for_each_test():
    t = pd.concat([plate(responders=8, seed=3, treatment="drugA"),
                   plate(n_ctrl=0, responders=0, seed=4, treatment="drugB")])
    res = rr.analyse(t)
    p = res.table.set_index("treatment")["hc_p"]
    q = res.table.set_index("treatment")["hc_q"]
    lo, hi = sorted(p)
    assert q.min() == pytest.approx(min(2 * lo, hi)) and q.max() == pytest.approx(hi)


def test_the_exceedance_test_alone_decides_the_verdict():
    """User decision: Test A decides; Higher Criticism and the upper-tail shape test never change the verdict. Each
    case puts the supporting tests on the other side of the line from the exceedance test, so a rule that let either
    decide would give a different answer."""
    unit = rr.Unit("all", "drug", [], [], True, True)
    unit.q = {"exceedance": 0.4, "hc": 0.01, "ad": 0.01}
    assert rr.interpret(unit, 0.05) == rr.NOT_DETECTED
    unit.q = {"exceedance": 0.01, "hc": 0.9, "ad": 0.9}
    assert rr.interpret(unit, 0.05) == rr.DETECTED
    unit.q = {"exceedance": float("nan"), "hc": 0.01, "ad": 0.01}
    assert rr.interpret(unit, 0.05) == rr.NOT_DETECTED
    assert rr.interpret(rr.Unit("all", "drug", [], [], False, False), 0.05) == rr.INSUFFICIENT


def test_responders_name_the_spiked_nanowells_with_their_crops():
    t = plate(responders=5, seed=6)
    spiked = t[(t["condition"] == "drug") & (t["nt"] == (t["n0"] * 2 ** 6.5).astype(int))]
    res = rr.analyse(t)
    flagged = set(zip(res.responders["well_key"], res.responders["nanowell_id"]))
    assert set(zip("P1:" + spiked["well_id"], spiked["nanowell_id"])) <= flagged
    assert res.responders["crop_path"].str.startswith("crops/").all()
    assert (res.responders["condition"] == "drug").all()


def test_the_log_carries_parameters_seed_and_exclusions():
    t = plate(n=100)
    t.loc[:4, "n0"] = 0
    res = rr.analyse(t, RareParams(seed=99, alpha0=0.2))
    log = res.log
    assert log["parameters"]["seed"] == 99 and log["parameters"]["alpha0"] == 0.2
    assert log["exclusions_by_reason"] == {N0_ZERO: 5}
    assert log["units"][0]["control_wells"] and set(log["units"][0]["thresholds"]) == set(res.prepared.strata)


def test_a_missing_control_condition_is_refused_by_name():
    with pytest.raises(ValueError, match="'ctrl'"):
        rr.analyse(plate(n=20), RareParams(control="ctrl"))


def test_by_plate_runs_each_plate_against_its_own_controls():
    t = pd.concat([plate(n=100, plate_id="P1"), plate(n=100, seed=9, plate_id="P2")])
    res = rr.analyse(t, RareParams(by_plate=True))
    assert sorted(res.table["plate"]) == ["P1", "P2"]
    assert all(w.plate == u.plate for u in res.units for w in u.controls + u.treated)


def test_export_writes_every_table_and_the_parameter_log(tmp_path):
    from review.features.nanowell.rare_export_compute import report_lines, write

    res = rr.analyse(plate(n=150, responders=5))
    paths = write(res, tmp_path)
    assert {p.name for p in paths} >= {"rare_per_treatment.csv", "rare_responders.csv", "rare_parameters.json",
                                       "rare_exclusions.csv", "rare_sensitivity.csv", "rare_report.txt"}
    assert json.loads((tmp_path / "rare_parameters.json").read_text())["parameters"]["percentile"] == 99.0
    text = "\n".join(report_lines(res))
    assert "anti-conservative" in text and "uncalibrated" in text


# ── validation ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_self_check_reports_false_positives_and_power_by_spike_count():
    from review.features.nanowell.rare_validate_compute import validate

    p = prepare(plate(n_ctrl=8, n_treat=0, n=300, seed=11),
                RareParams(validation_splits=10, validation_replicates=4, spike_counts=(1, 20)))
    v = validate(p)
    assert list(v.false_positive["test"]) == ["exceedance", "hc", "ad"]
    assert (v.false_positive["splits"] == 10).all()
    power = v.power.set_index(["test", "k"])["power"]
    assert power[("hc", 20)] == 1.0 and power[("hc", 20)] >= power[("hc", 1)]


def test_the_self_check_says_when_there_are_too_few_control_wells():
    from review.features.nanowell.rare_validate_compute import validate

    v = validate(prepare(plate(n_ctrl=3, n_treat=0, n=50)))
    assert v.false_positive.empty and any("needs at least 4" in n for n in v.notes)


# ── site metrics ────────────────────────────────────────────────────────────────────────────────────────────────
def test_from_site_metrics_takes_baseline_and_final_counts_and_flags_unmeasurable_sites():
    m = pd.DataFrame({"well": ["A1"] * 4, "nw_q": [0, 0, 1, 1], "nw_r": [0, 0, 0, 0],
                      "timepoint": ["t0", "t3", "t0", "t3"], "baseline_timepoint": ["t0"] * 4,
                      "time_h": [0.0, 72.0, 0.0, 72.0], "n0": [2, 2, 1, 1], "n": [2, 9, 1, np.nan],
                      "state": ["measured", "measured", "measured", "occluded"],
                      "fused": [False, False, False, False], "identity_ok": [True] * 4})
    t = from_site_metrics(m, {"A1": "control"}, "t3", plate_id="P9", crop_path=lambda w, q, r: f"{w}/{q}_{r}")
    assert list(t["nanowell_id"]) == ["0,0", "1,0"] and list(t["nt"][:1]) == [9]
    assert list(t["qc_state"]) == ["", "occluded"] and list(t["crop_path"]) == ["A1/0_0", "A1/1_0"]
    assert list(t["t_h"] - t["t0_h"]) == [72.0, 72.0]
    kept = prepare(t).kept
    assert list(kept["nanowell_id"]) == ["0,0"] and kept["fc"].iloc[0] == 4.5


def test_from_site_metrics_reads_occupancy_3_plus_as_a_value_not_a_missing_one():
    """Occupancy classes are the strings 0, 1, 2 and 3+; a numeric read made every 3+ nanowell NaN, and the analysis
    then left it out as having no value of the metric — the nanowells with the most cells dropped from a test of the
    top tail."""
    m = pd.DataFrame({"well": ["A1"] * 4, "nw_q": [0, 0, 1, 1], "nw_r": [0, 0, 0, 0],
                      "timepoint": ["t0", "t3", "t0", "t3"], "baseline_timepoint": ["t0"] * 4,
                      "time_h": [0.0, 72.0, 0.0, 72.0], "n0": [1, 1, 1, 1], "n": [1, 5, 1, 2],
                      "occupancy_class": ["1", "3+", "1", "2"], "state": ["measured"] * 4,
                      "fused": [False] * 4, "identity_ok": [True] * 4})
    t = from_site_metrics(m, {"A1": "control"}, "t3", plate_id="P9", crop_path=lambda w, q, r: "",
                          value_column="occupancy_class")
    assert list(t["value"]) == [3.0, 2.0]


def test_a_growth_rate_is_not_called_a_level_with_a_fold_change_form():
    from review.features.nanowell.rare_export_compute import _is_level

    assert not _is_level("Growth rate (per hour, from ln n)")
    assert _is_level("Cell count")


# ── figures ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_figures_draw_threshold_null_curves_and_named_crop_placeholders():
    from matplotlib.figure import Figure

    from review.features.nanowell import _rare_render as rd

    res = rr.analyse(plate(n=200, responders=6, seed=12))
    unit = res.units[0]
    fig = Figure()
    rd.render_ecdf(fig, unit, res.prepared.value_label)
    gids = {a.get_gid() for ax in fig.axes for a in ax.get_children()}
    assert {f"rare-threshold:{s}" for s in unit.thresholds} <= gids
    rd.render_exceedance(fig, unit)
    gids = {a.get_gid() for ax in fig.axes for a in ax.get_children()}
    assert "rare-null-band" in gids and all(f"rare-exceed-treated:{w.key}" in gids for w in unit.treated)
    rd.render_hc(fig, unit)
    gids = {a.get_gid() for ax in fig.axes for a in ax.get_children()}
    assert all(f"rare-hc-treated:{w.key}" in gids for w in unit.treated)
    first = res.responders.iloc[0]
    rd.render_crops(fig, res.responders.head(3),
                    lambda path: np.zeros((8, 8)) if path == first["crop_path"] else None)
    gids = {a.get_gid() for ax in fig.axes for a in ax.get_children()}
    assert f"rare-crop:{first['well_key']}:{first['nanowell_id']}" in gids
    assert sum(1 for g in gids if g and g.startswith("rare-crop-missing:")) == 2


def test_no_control_exceedance_reports_an_unbounded_odds_ratio_not_a_number():
    ctrl = [_well(k, np.r_[np.ones(99), 1.0]) for k in ("A", "B", "C")]
    treat = [_well(k, np.r_[np.ones(95), [50.0] * 5], "drug") for k in ("T", "U")]
    a = ex.exceedance_test(ctrl, treat, 99.0, None, bb_allowed=True, group_allowed=True)
    assert a.odds_ratio == math.inf and a.p < 0.05
    assert any("no control well has a nanowell above the threshold" in n for n in a.notes)


def _laid_out(res, width, lines=(), **kw):
    """*res*'s page drawn at *width* inches and the height it asked for: drawn once to learn the height, then again at
    it, as the page does once the canvas has grown (``_rare_controller.fit_canvas_height``)."""
    from matplotlib.figure import Figure

    from review.features.nanowell import _rare_render as rd

    fig = Figure(figsize=(width, 5.0))
    need = rd.render_rare_page(fig, res, lines, **kw)
    fig.set_size_inches(width, need)
    assert rd.render_rare_page(fig, res, lines, **kw) == pytest.approx(need)    # a fixed point: no redraw loop
    assert not rd.needs_relayout(fig) and rd.needed_height(fig) == pytest.approx(need)
    return fig


def _panel(fig, gid):
    return next(ax for ax in fig.axes if ax.get_gid() == gid)


def test_every_comparison_is_drawn_one_row_per_test_however_many_and_however_narrow():
    """User decision: each row holds the plots of one test only, headed by the test; a panel of one dot per well holds
    every comparison on one axes; a panel of curves is drawn once per comparison, as many across as the width holds and
    the rest wrapping — so the figure never grows sideways and no comparison is ever left off it. The figure is as tall
    as it needs, and nothing on it overlaps."""
    from review.features.nanowell import _rare_render as rd
    from review.features.nanowell import rare_export_compute as rx

    t = pd.concat([plate(n=120, responders=4, seed=s, treatment=f"drug{s}") if s == 0 else
                   plate(n_ctrl=0, n=120, responders=4, seed=s, treatment=f"drug{s}") for s in range(4)])
    res = rr.analyse(t)
    assert len(res.units) == 4
    lines = [f"line {i} " + "x" * 150 for i in range(12)]
    for width, columns in ((14.5, 3), (10.0, 2), (7.0, 2), (4.06, 1)):
        assert rd.columns_for(width) == columns
        fig = _laid_out(res, width, lines, note="a note")
        height = fig.get_figheight()
        gids = {ax.get_gid() for ax in fig.axes}
        keys = [row[0] for row in rx.TEST_ROWS]
        assert {f"nw-rare-shared:{k}" for k in ("exceedance", "hc", "shape")} <= gids
        assert {f"nw-rare-panel:{k}:{i}" for k in ("exceedance", "hc", "pooled") for i in range(4)} <= gids
        # Every comparison is named over the figure, and each named panel says which comparison it is.
        heads = sorted(t.get_text() for t in fig.texts if str(t.get_gid()).startswith("nw-rare-comparison-head:"))
        assert heads == [rx.comparison_title(res, i) for i in range(4)]
        assert [t.get_text() for t in fig.texts if t.get_gid() == "nw-rare-groups"][0].replace("\n", " ").startswith(
            "5 groups tested: control control (")
        # The test rows in order, each heading above its own panels and below the previous row's.
        tops = [next(t for t in fig.texts if t.get_gid() == f"nw-rare-test:{k}").get_position()[1] for k in keys]
        assert tops == sorted(tops, reverse=True), width
        for k, top, below in zip(keys, tops, [*tops[1:], -1.0]):
            row = [ax for ax in fig.axes if str(ax.get_gid()).startswith(("nw-rare-shared:", "nw-rare-panel:"))
                   and str(ax.get_gid()).split(":")[1] == k]
            assert row and all(below < ax.get_position().y0 and ax.get_position().y1 < top for ax in row), (width, k)
        # The panels of one row's wrap: as many across as the width holds.
        x0s = {round(_panel(fig, f"nw-rare-panel:pooled:{i}").get_position().x0, 4) for i in range(4)}
        assert len(x0s) == columns, width
        panels = [ax for ax in fig.axes if str(ax.get_gid()).startswith(("nw-rare-shared:", "nw-rare-panel:"))]
        boxes = [ax.get_position() for ax in panels]
        assert all(b.y0 >= 0 and b.y1 <= 1 and b.x0 >= 0 and b.x1 <= 1 for b in boxes), width
        assert all(b.height * height >= 0.9 and b.width * width >= 2.4 for b in boxes), width
        for i, one in enumerate(boxes):                                     # no panel on another
            for other in boxes[i + 1:]:
                assert one.x1 <= other.x0 or other.x1 <= one.x0 or one.y1 <= other.y0 or other.y1 <= one.y0, width
        texts = [t for t in fig.texts if t.get_gid() == "nw-rare-line"]
        assert texts and max(t.get_position()[1] for t in texts) < min(b.y0 for b in boxes), width
        assert not any("not drawn" in t.get_text() for t in fig.texts), width


def _plate_wells(t: pd.DataFrame) -> pd.DataFrame:
    """*t* with its wells renamed to plate positions: control wells along row A, treatment wells along row B, so
    each has the colour the plate gives it."""
    t = t.copy()
    rows = {"control": "A"}
    for cond in t["condition"].unique():
        names = sorted(t.loc[t["condition"] == cond, "well_id"].unique())
        row = rows.get(cond, "B")
        t.loc[t["condition"] == cond, "well_id"] = t.loc[t["condition"] == cond, "well_id"].map(
            {name: f"{row}{i + 1:02d}" for i, name in enumerate(names)})
    return t


def test_the_page_figure_colours_strata_by_occupancy_and_treatment_wells_as_the_plate_does():
    """The reported defect: the ECDF's strata were drawn in the plate's first well colours, so N0 1 read as A01, and
    the treatment wells took the first slots too (B01 in A01's colour). Strata are the occupancy ramp — screen
    shades on screen, publication shades after a capture's repaint, legend included — and each treatment well is
    its own plate colour."""
    from matplotlib.colors import to_hex
    from review.domain.selections_model import color_for_well
    from review.features.nanowell import _rare_render as rd
    from review.features.nanowell.renderer import _is_publication
    from ui.theme import WELL_COLORS_TUPLE, Colors, CPub
    from widgets.plot_card import repaint_theme_pairs

    res = rr.analyse(_plate_wells(plate(n_ctrl=3, n_treat=2, n=200, responders=6, seed=12)),
                     RareParams(strata_edges=(1, 2, 3, 4)))
    unit = res.units[0]
    strata = rd._strata(unit)
    assert len(strata) == 4                                    # n0 1…5: strata 1, 2, 3 and 4 and over
    fig = _laid_out(res, 14.5, ["verdict"])
    ecdf, exceed, hc_ax = (_panel(fig, gid) for gid in ("nw-rare-panel:exceedance:0", "nw-rare-shared:exceedance",
                                                         "nw-rare-panel:hc:0"))

    def treatment_lines():
        return {ln.get_gid().split(":", 1)[1]: to_hex(ln.get_color()).upper() for ln in ecdf.lines
                if (ln.get_gid() or "").startswith("rare-ecdf-treatment:")}

    drawn_on = CPub if _is_publication(ecdf) else Colors       # drawn in the shades of the figure's own ground
    assert treatment_lines() == {s: drawn_on.occupancy_ramp[i].upper() for i, s in enumerate(strata)}
    repaint_theme_pairs(ecdf, "screen")
    assert treatment_lines() == {s: Colors.occupancy_ramp[i].upper() for i, s in enumerate(strata)}
    assert not set(treatment_lines().values()) & {c.upper() for c in WELL_COLORS_TUPLE}
    repaint_theme_pairs(ecdf, "publication")
    assert treatment_lines() == {s: CPub.occupancy_ramp[i].upper() for i, s in enumerate(strata)}
    # The strata are labelled on the panel, each with a dot in its shade, which repaints with the lines.
    dots = {c.get_gid().split(":", 1)[1]: to_hex(c.get_facecolors()[0]).upper() for c in ecdf.collections
            if (c.get_gid() or "").startswith("rare-ecdf-dot:")}
    assert dots == {s: CPub.occupancy_ramp[i].upper() for i, s in enumerate(strata)}
    labels = {t.get_gid().split(":", 1)[1]: t.get_text() for t in ecdf.texts
              if (t.get_gid() or "").startswith("rare-ecdf-label:")}
    assert labels == {s: rd._stratum_name(s) for s in strata} and ecdf.get_legend() is None
    assert [t.get_text() for t in ecdf.texts if t.get_gid() == "rare-cutoff-label"] == ["control cutoff"]
    threshold = next(ln for ln in ecdf.lines if ln.get_gid() == f"rare-threshold:{strata[0]}")
    assert to_hex(threshold.get_color()).upper() == CPub.occupancy_ramp[0].upper()

    for well in ("B01", "B02"):
        key = next(w.key for w in unit.treated if w.key.endswith(well))
        dot = next(c for c in exceed.collections if c.get_gid() == f"rare-exceed-treated:{key}")
        assert to_hex(dot.get_facecolors()[0]).upper() == color_for_well(well).upper()
        curve = next(ln for ln in hc_ax.lines if ln.get_gid() == f"rare-hc-treated:{key}")
        assert to_hex(curve.get_color()).upper() == color_for_well(well).upper()
    assert color_for_well("B01").upper() != WELL_COLORS_TUPLE[0].upper()   # the fixture tells right from wrong


def test_a_phone_s_figure_holds_one_comparison_across_and_keeps_its_count_labels_inside():
    """On a phone (a figure about 4 in wide) each per-comparison panel takes the figure's width, one under another, and
    a shared panel of one dot per well keeps headroom over its highest dot for that dot's count and over the groups for
    their names: every label inside its axes."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    res = rr.analyse(_plate_wells(plate(n_ctrl=3, n_treat=2, n=200, responders=6, seed=12)),
                     RareParams(strata_edges=(1, 2, 3, 4)))
    fig = _laid_out(res, 4.06, ["drug: 2 vs 3 control wells"], note="a note")
    small = [_panel(fig, f"nw-rare-panel:{k}:0") for k in ("exceedance", "hc", "pooled")]
    assert len({round(ax.get_position().x0, 6) for ax in small}) == 1
    assert all(ax.get_position().width * 4.06 >= 2.5 for ax in small)
    renderer = FigureCanvasAgg(fig).get_renderer()
    for key in ("exceedance", "hc", "shape"):
        ax = _panel(fig, f"nw-rare-shared:{key}")
        box = ax.get_window_extent(renderer)
        labels = [t for t in ax.texts if str(t.get_gid()).endswith(("-count", "-group"))]
        assert labels, key
        assert all(t.get_window_extent(renderer).y1 <= box.y1 + 1 for t in labels), key
    # The names over the groups never run into one another.
    groups = sorted((t.get_window_extent(renderer) for t in _panel(fig, "nw-rare-shared:exceedance").texts
                     if str(t.get_gid()).endswith("-group")), key=lambda b: b.x0)
    assert all(a.x1 <= b.x0 + 1 for a, b in zip(groups, groups[1:]))


# ── what the result says, and what the figure draws (tracker RARE_RESPONDERS_UX_TRACKER.md) ──────────────────────
def _small_plate(*, spiked: bool, n_ctrl: int = 3, n_treat: int = 2, seed: int = 3) -> pd.DataFrame:
    """Whole-number fold changes (2×, 3×, 4× of 1–3 starting cells), as a real count plate has, on wells named as
    the plate names them; with *spiked*, nanowells 5, 17, 33 and 48 of each drug well grow 30-fold."""
    rng = np.random.default_rng(seed)
    rows = []
    for well in [f"A{i + 1:02d}" for i in range(n_ctrl)] + [f"B{i + 1:02d}" for i in range(n_treat)]:
        n0 = rng.integers(1, 4, 60)
        nt = n0 * rng.integers(2, 5, 60)
        if spiked and well.startswith("B"):
            for q in (5, 17, 33, 48):
                nt[q] = n0[q] * 30
        rows += [{"plate_id": "P", "well_id": well, "condition": "ctrl" if well.startswith("A") else "drug",
                  "nanowell_id": str(i), "n0": int(n0[i]), "nt": int(nt[i]), "t0_h": 0.0, "t_h": 5.0,
                  "crop_path": ""} for i in range(60)]
    return pd.DataFrame(rows)


def _deciding(unit) -> str:
    """The deciding line *unit* should print: its own p, formatted as the figure formats it. The beta-binomial fit is
    iterative and, on a separated plate, its last digit depends on the platform's numeric libraries (0.00072 here,
    0.000717 on the CI runner), so tests check the sentence against the result and the value against 0.00072 to 2%."""
    from review.features.nanowell import rare_export_compute as rx

    assert unit.primary.p == pytest.approx(0.00072, rel=0.02)
    return f"Deciding: exceedance test p = {rx._num(unit.primary.p)}."


def test_a_plate_with_no_responders_flags_nothing_and_says_the_test_could_not_run():
    """The reported defect: the null plate listed 15 "flagged" nanowells — Higher Criticism's candidates, which every
    well has — beside a verdict of none. Only nanowells past the cutoff are flagged (user decision), and with none
    past it in any well the deciding test says it was not run rather than printing dashes."""
    from review.features.nanowell import rare_export_compute as rx

    res = rr.analyse(_small_plate(spiked=False), rr.RareParams(control="ctrl"))
    unit = res.units[0]
    assert len(res.responders) == 0 and unit.interpretation == rr.NOT_DETECTED
    assert any(len(unit.hc[w.key].candidates) for w in unit.treated)      # the candidates exist; they are not flagged
    text = rx.unit_text(res, unit)
    assert text["headline"] == ("drug: rare responders not detected. 0 of 120 drug nanowells (0.0%) rose above the "
                                "control cutoff (its top 1%) on fold change (Nt / N0), against 0 of 180 control "
                                "nanowells (0.0%) · 2 drug vs 3 control wells.")
    assert text["lines"][0] == "Deciding: exceedance test not tested — no nanowell in any well was past the cutoff."


def test_the_spiked_plate_leads_with_counts_and_names_every_limit_of_the_design():
    from review.features.nanowell import rare_export_compute as rx

    res = rr.analyse(_small_plate(spiked=True), rr.RareParams(control="ctrl"))
    unit = res.units[0]
    assert sorted(res.responders["nanowell_id"].astype(int)) == sorted([5, 17, 33, 48] * 2)
    text = rx.unit_text(res, unit)
    assert text["headline"].startswith("drug: rare responders detected. 8 of 120 drug nanowells (6.7%) rose above "
                                       "the control cutoff (its top 1%) on fold change (Nt / N0), against 0 of 180 "
                                       "control nanowells (0.0%)")
    assert text["lines"] == [
        _deciding(unit),
        # Unstratified, the control pool (180) is larger than a well (60), so every spiked nanowell falls at p ≤ 1/n,
        # where HC+ does not look: its p is 0.9, and the third caveat says why.
        "Supporting, not deciding: Higher Criticism p = 0.9 · shape test p = 0.1 (the lowest possible with these "
        "wells)."]
    assert text["caveats"] == [
        "With 3 control and 2 drug wells, Higher Criticism and the shape test cannot go below p = 0.1, so they can "
        "neither confirm nor rule out anything here.",
        "No control nanowell was past the cutoff, so the spread between wells could not be estimated: the "
        "exceedance test's p treats nanowells as independent and overstates the evidence (on wells alone, p ≥ 0.1).",
        "Higher Criticism cannot see the 8 most extreme drug nanowell(s) (it leaves out p ≤ 1/n), so it says nothing "
        "about them."]


def test_higher_criticism_s_blind_spot_is_named_where_it_hides_the_responders():
    """User decision: keep HC+, and warn. With 6 control wells the pool is larger than a well, every spiked nanowell
    falls at p ≤ 1/n, HC+ leaves them all out (p 0.93) — and the figure says so."""
    from review.features.nanowell import rare_export_compute as rx

    res = rr.analyse(_small_plate(spiked=True, n_ctrl=6, n_treat=3), rr.RareParams(control="ctrl"))
    unit = res.units[0]
    assert unit.interpretation == rr.DETECTED and unit.hc_perm.p > 0.5
    assert ("Higher Criticism cannot see the 12 most extreme drug nanowell(s) (it leaves out p ≤ 1/n), so it says "
            "nothing about them.") in rx.caveats(unit, res.prepared.params)


def test_the_upper_tail_shape_test_sees_mass_beyond_the_control_maximum():
    """With whole-number fold changes (2×, 3×, 4×) the control pool had no value above its median and below its top,
    and the top carried no weight (1 − F0 = 0 there), so the statistic summed over nothing and a nanowell beyond
    every control counted for nothing. F0 = #/(N + 1) gives the top weight: the 30-fold spikes now count, and the
    test runs on the 2 + 2 design too. A pool of one value has no tail and stays undefined, not zero (CLAUDE.md §7)."""
    from review.features.nanowell import rare_ad_compute as ad

    ref = np.array([2.0, 3.0, 4.0, 4.0])
    assert ad.a2r_stratum(np.array([2.0, 4.0, 60.0]), ref) > ad.a2r_stratum(np.array([2.0, 3.0, 4.0]), ref) >= 0
    assert math.isnan(ad.a2r_stratum(np.array([9.0]), np.array([3.0, 3.0, 3.0])))
    spiked = rr.analyse(_small_plate(spiked=True), rr.RareParams(control="ctrl")).units[0]
    null = rr.analyse(_small_plate(spiked=False), rr.RareParams(control="ctrl")).units[0]
    assert spiked.a2_perm.p == pytest.approx(spiked.a2_perm.min_p)        # the strongest the design allows
    assert null.a2_perm.p > 0.5
    two = rr.analyse(_small_plate(spiked=True, n_ctrl=2), rr.RareParams(control="ctrl")).units[0]
    assert np.isfinite(two.a2_perm.p)


def test_a_relabeling_whose_statistic_is_undefined_does_not_make_p_smaller():
    """Four wells, two treated (C, D): six labelings. The statistic is 2 for C, 0 for D and undefined for A and B, so
    the observed mean is 1; {A,C} and {B,C} score 2, {A,D} and {B,D} score 0, and {A,B} is undefined. Counting the
    undefined labeling as smaller gave p = 3/6; it cannot be ruled out as at least as extreme, so p = 4/6."""
    import review.features.nanowell.rare_null_compute as rn

    wells = [_well(k, [1.0]) for k in "ABCD"]
    values = {"A": math.nan, "B": math.nan, "C": 2.0, "D": 0.0}
    perm = rn.permutation_test(wells, {"C", "D"}, lambda well, _pooled: values[well.key], 100, 0)
    assert perm.n_labelings == 6 and perm.observed == 1.0
    assert perm.p == pytest.approx(4 / 6)


def test_the_figure_draws_counts_and_fold_changes_not_p_values_and_logarithms():
    from review.features.nanowell import rare_export_compute as rx

    res = rr.analyse(_small_plate(spiked=True), rr.RareParams(control="ctrl"))
    fig = _laid_out(res, 14.5)
    fig.canvas.draw()
    ecdf, exceed, hc_ax = (_panel(fig, gid) for gid in ("nw-rare-panel:exceedance:0", "nw-rare-shared:exceedance",
                                                         "nw-rare-panel:hc:0"))
    assert ecdf.get_title() == "Fold change (Nt / N0)\nsolid: drug · dotted: control · dashed: the cutoff"
    ticks = [t.get_text() for t in ecdf.get_xticklabels() if t.get_text()]
    assert {"2×", "4×", "8×"} <= set(ticks) and all(t.endswith("×") for t in ticks)      # fold changes, not log₂
    counts = sorted(t.get_text() for t in exceed.texts if t.get_gid() == "rare-exceed-count")
    assert counts == ["0/60", "0/60", "0/60", "4/60", "4/60"]
    assert not any(t.get_text().startswith("p=") for t in exceed.texts)
    assert exceed.get_ylim()[0] == 0
    assert hc_ax.get_title() == "colour: drug wells · grey: control"          # its p is in its row and caption
    heads = [t.get_text().replace("\n", " ") for t in fig.texts if t.get_gid() == "nw-rare-unit"]
    assert heads and heads[0].startswith("drug: rare responders detected. 8 of 120")
    # The deciding test's p is in its own row's heading, and in the caption of each of its panels.
    p_line = next(t.get_text() for t in fig.texts if t.get_gid() == "nw-rare-test-p:exceedance")
    assert p_line == f"drug: {_deciding(res.units[0])[len('Deciding: exceedance test '):].rstrip('.')}"
    caption = next(t.get_text() for t in fig.texts if t.get_gid() == "nw-rare-comparison:exceedance:0")
    assert caption.replace("\n", " ") == f"{rx.comparison_title(res, 0)} · {p_line[len('drug: '):]}"
    panels = [ax for ax in fig.axes if str(ax.get_gid()).startswith(("nw-rare-shared:", "nw-rare-panel:"))]
    assert min(t.get_position()[1] for t in fig.texts if t.get_gid() == "nw-rare-unit") > max(
        ax.get_position().y1 for ax in panels)                          # the answer above the panels
    lines = [t for t in fig.texts if t.get_gid() == "nw-rare-line"]
    assert lines and max(t.get_position()[1] for t in lines) < min(ax.get_position().y0 for ax in panels)


def test_the_figure_draws_every_caveat_of_every_comparison_whole():
    """Nothing is cut from the figure any more — it is as tall as it needs and the page scrolls — so every caveat of
    every comparison is drawn, each named by its comparison, before the lines and the note."""
    from review.features.nanowell import rare_export_compute as rx

    res = rr.analyse(_small_plate(spiked=True), rr.RareParams(control="ctrl"))
    fig = _laid_out(res, 4.06, ["a line"], note="pointer")
    drawn = " ".join(t.get_text().strip() for t in fig.texts if t.get_gid() == "nw-rare-line")
    caveats = rx.unit_text(res, res.units[0])["caveats"]
    assert caveats
    for caveat in caveats:
        assert f"⚠ Comparison 1 of 1 (drug): {caveat}" in drawn or " ".join(
            f"⚠ Comparison 1 of 1 (drug): {caveat}".split()) in " ".join(drawn.split())
    assert drawn.endswith("a line pointer")


def test_the_value_is_the_page_s_metric_with_no_strata_and_direction_reads_either_tail():
    """User decision: the page's Metric drives the test (a ``value`` column), nanowells are not stratified, and a
    direction chooses the tail. A bottom-tail plate (four nanowells per drug well dropping to a tenth of anything in
    control) is found looking lower and missed looking higher; the figure draws the values as measured."""

    from review.features.nanowell import rare_export_compute as rx
    from review.features.nanowell.rare_data_compute import ALL

    rng = np.random.default_rng(5)
    rows = []
    for well in ("A01", "A02", "A03", "B01", "B02"):
        # Drug wells' ordinary nanowells sit inside the control's range (never below its bottom 1%), so exactly the
        # four dropped ones are past the cutoff.
        value = rng.uniform(100.0, 200.0, 60) if well < "B" else rng.uniform(150.0, 200.0, 60)
        if well.startswith("B"):
            value[[5, 17, 33, 48]] = 9.0
        rows += [{"plate_id": "P", "well_id": well, "condition": "ctrl" if well < "B" else "drug",
                  "nanowell_id": str(i), "value": float(value[i]), "crop_path": ""} for i in range(60)]
    table = pd.DataFrame(rows)
    lower = rr.analyse(table, rr.RareParams(control="ctrl", direction="lower"), value_label="GFP brightness")
    higher = rr.analyse(table, rr.RareParams(control="ctrl"), value_label="GFP brightness")
    assert lower.prepared.strata == [ALL] and lower.prepared.sign == -1
    assert lower.units[0].interpretation == rr.DETECTED and higher.units[0].interpretation == rr.NOT_DETECTED
    assert sorted(lower.responders["nanowell_id"].astype(int)) == sorted([5, 17, 33, 48] * 2)
    assert (lower.responders["fc"] == 9.0).all()                         # flagged at the value as measured
    text = rx.unit_text(lower, lower.units[0])
    assert text["headline"].startswith("drug: rare responders detected. 8 of 120 drug nanowells (6.7%) fell below "
                                       "the control cutoff (its bottom 1%) on GFP brightness")
    assert any(c.startswith("GFP brightness is a level, not a change from the Baseline: nanowells that started "
                            "lower") for c in text["caveats"])
    fig = _laid_out(lower, 14.5)
    ecdf = _panel(fig, "nw-rare-panel:exceedance:0")
    cut = next(ln for ln in ecdf.lines if ln.get_gid() == f"rare-threshold:{ALL}")
    assert 2 ** cut.get_xdata()[0] > 90                               # the cutoff drawn at a measured brightness
    drawn = next(ln for ln in ecdf.lines if ln.get_gid() == f"rare-ecdf-treatment:{ALL}")
    assert 2 ** min(drawn.get_xdata()) == pytest.approx(9.0)          # the low responders, as measured



def test_a_design_too_small_for_the_deciding_test_cannot_be_decided_rather_than_not_detected():
    """Reported: two control and two treatment wells. The beta-binomial regression needs three control wells, so the
    deciding test has no group p — and the verdict read "not detected" beside 8 of 120 drug nanowells past the cutoff
    against 0 of 120 in control. That is a test that did not run, shown as a negative (CLAUDE.md §7). It says it cannot
    be decided, and why; with nothing past the cutoff in any well, "not detected" is still true and still said."""
    from review.features.nanowell import rare_export_compute as rx

    spiked = rr.analyse(_small_plate(spiked=True, n_ctrl=2), rr.RareParams(control="ctrl"))
    unit = spiked.units[0]
    assert unit.interpretation == rr.UNDECIDED
    text = rx.unit_text(spiked, unit)
    # Asked for: the headline says why, not only the line under the panels.
    assert text["headline"].startswith("drug: cannot be decided with these wells — the deciding test needs at least 3 "
                                       "control wells (this design has 2). 8 of 120 drug nanowells (6.7%)")
    assert text["lines"][0] == ("Deciding: exceedance test has no group p — it needs at least 3 control wells (this "
                                "design has 2). With fewer, it gives each well's own p only (Details).")
    null = rr.analyse(_small_plate(spiked=False, n_ctrl=2), rr.RareParams(control="ctrl")).units[0]
    assert null.interpretation == rr.NOT_DETECTED


# ── What the design cannot see (rare_design_compute) ───────────────────────────────────────────────────────────
def _value_table(wells):
    rows = [{"plate_id": "p", "well_id": w, "condition": c, "nanowell_id": str(i), "value": float(v)}
            for w, c, values in wells for i, v in enumerate(values)]
    return pd.DataFrame(rows)


def test_a_cutoff_on_a_floor_tie_is_undecided_not_a_negative():
    """Responders lower on a fold change with a few dead control nanowells (fc = 0, 3 %, more than the 1 % tail):
    the cutoff lands on 0 and nothing can be strictly below it, so every count is 0. That is a test that could not
    count, not a finding of none — while 40 % of the treatment's nanowells died."""
    rng = np.random.default_rng(0)

    def well(p_dead, n=200):
        v = rng.lognormal(0.7, 0.3, n)
        v[rng.random(n) < p_dead] = 0.0
        return v

    wells = [(f"C{i}", "ctrl", well(0.03)) for i in range(4)] + [(f"T{i}", "drug", well(0.40)) for i in range(4)]
    res = rr.analyse(_value_table(wells), RareParams(control="ctrl", direction="lower"),
                     value_label="Count fold change")
    unit = res.units[0]
    assert sum(c.k for c in unit.primary.counts) == 0
    assert unit.interpretation == rr.UNDECIDED
    assert "sits on a tie" in unit.tie_note
    from review.features.nanowell.rare_export_compute import caveats
    assert unit.tie_note in caveats(unit, res.prepared.params, "Count fold change")


def test_a_continuous_cutoff_is_not_called_a_tie():
    rng = np.random.default_rng(0)
    wells = [(f"C{i}", "ctrl", rng.lognormal(0.7, 0.3, 200)) for i in range(4)] + \
            [(f"T{i}", "drug", rng.lognormal(0.7, 0.3, 200)) for i in range(4)]
    unit = rr.analyse(_value_table(wells), RareParams(control="ctrl"), value_label="Count fold change").units[0]
    assert unit.tie_note == ""
    assert unit.interpretation == rr.NOT_DETECTED


def _seeded(seed, lam_ctrl, lam_drug):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(8):
        lam, cond = (lam_ctrl, "ctrl") if i < 4 else (lam_drug, "drug")
        n0 = rng.poisson(lam, 300)
        n0 = n0[n0 > 0][:250]
        nt = np.array([rng.poisson(3, k).sum() for k in n0])     # the same growth in both conditions
        rows += [("p", f"W{i}", cond, str(j), a, b, b / a) for j, (a, b) in enumerate(zip(n0, nt))]
    return pd.DataFrame(rows, columns=["plate_id", "well_id", "condition", "nanowell_id", "n0", "nt", "value"])


def test_seeding_that_differs_between_the_sides_is_said_beside_the_verdict():
    """The page tests its Metric with no grouping by starting cells. A treatment seeded more sparsely than its
    control (1.2 against 3.5 cells), with identical growth, puts more nanowells in the fold change's top tail by
    chance: the unstratified test calls it detected, and the figure must say why that is not to be trusted."""
    res = rr.analyse(_seeded(0, 3.5, 1.2), RareParams(control="ctrl"), value_label="Count fold change")
    unit = res.units[0]
    assert unit.interpretation == rr.DETECTED            # the verdict stands; the caveat is what reaches the reader
    assert unit.loading_note.startswith("Control and treatment nanowells started with different numbers of cells")
    from review.features.nanowell.rare_export_compute import caveats
    assert unit.loading_note in caveats(unit, res.prepared.params, "Count fold change")


def test_equal_seeding_says_nothing_about_it():
    unit = rr.analyse(_seeded(0, 2.0, 2.0), RareParams(control="ctrl"), value_label="Count fold change").units[0]
    assert unit.loading_note == ""


def test_one_control_well_is_not_reported_as_zero_of_zero_control_nanowells():
    """A lone control well is scored leave-one-out against an empty pool: its counts are 0 of 0, which is the absence
    of a comparison, not a count. The headline, the shape test's line and the exceedance note each said the wrong
    thing about it — "0 of 0 control nanowells (—)", "too few distinct fold changes", "per-well empirical p-values
    only" over a column of NaN."""
    from review.features.nanowell import rare_export_compute as rx

    res = rr.analyse(_null_plate(1, 3))
    unit = res.units[0]
    head = rx.headline(unit, res.prepared.params)
    assert "0 of 0 control" not in head and "no control null" in head
    report = "\n".join(rx._unit_lines(res, unit, res.table.iloc[0]))
    assert rx.NOT_RUN in report and rx.C_UNDEFINED not in report
    assert not any("per-well empirical p-values only" in n for n in unit.primary.notes)
    assert any("no control null" in n for n in unit.primary.notes)


def test_under_responders_lower_the_flagged_rows_read_in_the_metric_s_own_sign():
    """The analysis negates values to read the bottom tail as a top tail. The flagged-nanowell table and the parameter
    log wrote those negated numbers: a fold change of 0.98 as value -0.98 against a threshold of -1.03, "above" a
    cutoff it was below. They are written as the user reads them."""
    rng = np.random.default_rng(2)
    wells = [(f"C{i}", "ctrl", rng.lognormal(0.7, 0.3, 200)) for i in range(4)]
    for i in range(4):
        v = rng.lognormal(0.7, 0.3, 200)
        v[:12] = 0.2                                     # a few nanowells far below the control's bottom 1 %
        wells.append((f"T{i}", "drug", v))
    res = rr.analyse(_value_table(wells), RareParams(control="ctrl", direction="lower"),
                     value_label="Count fold change")
    flagged = res.responders
    assert len(flagged)
    assert (flagged["value"] == flagged["fc"]).all()
    assert (flagged["value"] < flagged["threshold"]).all() and (flagged["threshold"] > 0).all()
    assert all(t > 0 for t in res.log["units"][0]["thresholds"].values())


# ── A control with nothing left: say what excluded it, never "no such condition" with an empty list ─────────────

def _all_excluded_table(control_value, treated_value):
    import numpy as np

    rows = []
    for well, condition in (("A01", "ctrl"), ("A02", "ctrl"), ("B01", "drug"), ("B02", "drug")):
        value = control_value if condition == "ctrl" else treated_value
        rows += [{"plate_id": "p", "well_id": well, "condition": condition, "nanowell_id": f"{q},0",
                  "value": np.nan if value is None else value} for q in range(10)]
    return pd.DataFrame(rows)


def test_every_nanowell_excluded_names_the_exclusions_not_the_control():
    """Reported: "no well is labelled with control condition 'xxx'; conditions found: " with nothing listed. Every
    nanowell had been excluded (here: no value of the Metric at the Timepoint), so no well was left to carry a
    condition, and the message blamed the control's label for what the exclusions did."""
    from review.features.nanowell.rare_data_compute import VALUE_MISSING

    with pytest.raises(ValueError) as caught:
        rr.analyse(_all_excluded_table(None, None), rr.RareParams(control="ctrl"))
    assert str(caught.value) == ("every one of the 40 nanowells was excluded before testing, so nothing is left to "
                                 f"test ({VALUE_MISSING}: 40)")


def test_a_control_whose_every_nanowell_was_excluded_says_so():
    from review.features.nanowell.rare_data_compute import VALUE_MISSING

    with pytest.raises(ValueError) as caught:
        rr.analyse(_all_excluded_table(None, 2.0), rr.RareParams(control="ctrl"))
    assert str(caught.value) == ("every one of the 20 nanowells of the control 'ctrl' was excluded before testing "
                                 f"({VALUE_MISSING}: 20)")


def test_a_control_no_well_is_labelled_with_still_lists_the_conditions():
    with pytest.raises(ValueError) as caught:
        rr.analyse(_all_excluded_table(2.0, 2.0), rr.RareParams(control="nope"))
    assert str(caught.value) == "no well is labelled with the control condition 'nope'; conditions found: ctrl, drug"


# ── Higher Criticism refused: the shape test still runs, and still says what limits it ─────────────────────────────

def _hc_refused_result():
    """Three control and three treatment wells of eight nanowells each: α₀·n = 0.8 < 1, so HC+ has no index to take
    its maximum over in any well and every labeling's HC is undefined. The treatment wells hold two nanowells far
    above every control value, so the upper-tail shape test has something to find."""
    rows = []
    for well, condition in (("A01", "ctrl"), ("A02", "ctrl"), ("A03", "ctrl"),
                            ("B01", "drug"), ("B02", "drug"), ("B03", "drug")):
        values = [1.0 + 0.25 * q + 0.01 * int(well[-1]) for q in range(8)]
        if condition == "drug":
            values[:2] = [20.0, 21.0]
        rows += [{"plate_id": "p", "well_id": well, "condition": condition, "nanowell_id": f"{q},0", "value": v}
                 for q, v in enumerate(values)]
    return rr.analyse(pd.DataFrame(rows), rr.RareParams(control="ctrl"))


def test_the_shape_test_runs_when_higher_criticism_is_refused():
    from review.features.nanowell.rare_export_compute import supporting_line

    result = _hc_refused_result()
    unit = result.units[0]
    assert all(math.isnan(h.value) for h in unit.hc.values())                  # HC refused in every well
    assert math.isnan(unit.hc_perm.p)
    assert unit.a2_perm.p == pytest.approx(0.05) and unit.a2_perm.min_p == pytest.approx(0.05)   # 1 of 20 labelings
    assert unit.a2_p == {"p:B01": 0.25, "p:B02": 0.25, "p:B03": 0.25}
    assert supporting_line(unit, result.prepared.params) == (
        "Supporting, not deciding: Higher Criticism not computable · shape test p = 0.05 (the lowest possible "
        "with these wells).")


def test_the_shape_test_s_floor_is_said_when_higher_criticism_has_none():
    """The floor caveat was read off HC's permutation alone, so with HC refused it vanished — though the shape test,
    which did run, cannot go below p = 0.05 with three wells a side and so cannot be significant at an FDR of 0.05."""
    from review.features.nanowell.rare_export_compute import caveats

    result = _hc_refused_result()
    assert ("With 3 control and 3 drug wells, the shape test cannot go below p = 0.05, so it can neither confirm "
            "nor rule out anything here.") in caveats(result.units[0], result.prepared.params)


# ── The shape test's region on the cumulative panel ──────────────────────────────────────────────────────────────

def test_the_pooled_row_draws_the_gap_its_two_sided_test_sums_and_not_the_curves_again():
    """User decision: the pooled shape test's row draws, per comparison, the gap between the pooled control's and the
    group's share of nanowells at or below each value — what that two-sided test squares and sums — and not the two
    cumulative curves, which the exceedance row already draws (they had been drawn twice on the page). The gap is
    checked value by value against shares counted here, and is shaded between it and 0 on both sides."""
    from matplotlib.collections import PolyCollection

    res = rr.analyse(plate(responders=40, seed=1))
    fig = _laid_out(res, 14.5)
    unit = res.units[0]
    pooled = _panel(fig, "nw-rare-panel:pooled:0")
    assert not [ln for ln in pooled.lines if str(ln.get_gid()).startswith("rare-ecdf-")]     # not the curves again
    assert not [ln for ln in pooled.lines if str(ln.get_gid()).startswith("rare-threshold")]
    (line,) = [ln for ln in pooled.lines if ln.get_gid() == "rare-pooled-gap-line:all"]
    control = np.sort(np.concatenate([w.values["all"] for w in unit.controls]))
    treated = np.sort(np.concatenate([w.values["all"] for w in unit.treated]))
    x = np.unique(np.concatenate([control, treated]))
    expected = np.array([(control <= v).mean() - (treated <= v).mean() for v in x])
    keep = x > 0                                                       # the log axis leaves a fold change of 0 out
    assert np.allclose(line.get_xdata(), np.log2(x[keep])) and np.allclose(line.get_ydata(), expected[keep])
    assert (expected[keep] > 0).any() and (expected[keep] < 0).any()  # the fixture's gap goes both ways
    shaded = [c for c in pooled.collections if isinstance(c, PolyCollection) and c.get_gid() == "rare-pooled-gap:all"]
    assert len(shaded) == 1
    ys = np.concatenate([path.vertices[:, 1] for path in shaded[0].get_paths()])
    assert ys.min() == pytest.approx(expected[keep].min()) and ys.max() == pytest.approx(expected[keep].max())
    low, high = pooled.get_ylim()
    assert low == pytest.approx(-high) and high >= np.abs(expected).max()
    assert pooled.get_title().startswith("gap: control − drug\n")
    # The exceedance row keeps the cumulative curves and the cutoff.
    exceed = _panel(fig, "nw-rare-panel:exceedance:0")
    assert {ln.get_gid() for ln in exceed.lines} >= {"rare-ecdf-control:all", "rare-ecdf-treatment:all",
                                                     "rare-threshold:all"}


def test_each_test_is_one_framed_block_with_its_role_on_its_edge():
    """User decision: the plots of one test read as one block, visibly apart from the next — a frame around each
    test's heading, p line and every panel of its row, with a bar on its left edge in the colour of its role (accent:
    decides the verdict; secondary ink: supporting; warning amber: description only). No two blocks overlap, and the
    frame's outline repaints with the card's theme."""
    from ui.theme import Colors
    from review.features.nanowell import rare_export_compute as rx
    from widgets.plot_card import repaint_theme_ink

    t = pd.concat([plate(n=120, responders=4, seed=s, treatment=f"drug{s}") if s == 0 else
                   plate(n_ctrl=0, n=120, responders=4, seed=s, treatment=f"drug{s}") for s in range(4)])
    res = rr.analyse(t)
    for width in (14.5, 4.06):
        fig = _laid_out(res, width)
        spans = {}
        for key, *_rest in rx.TEST_ROWS:
            fills = [p for p in fig.patches if p.get_gid() == f"nw-rare-block:{key}"]
            assert len(fills) == 1, (width, key, len(fills))
            fill = fills[0]
            y0, y1 = fill.get_y(), fill.get_y() + fill.get_height()
            spans[key] = (y0, y1)
            heading = next(t for t in fig.texts if t.get_gid() == f"nw-rare-test:{key}").get_position()[1]
            assert y0 < heading < y1, (width, key)
            row = [ax for ax in fig.axes if str(ax.get_gid()).startswith(("nw-rare-shared:", "nw-rare-panel:"))
                   and str(ax.get_gid()).split(":")[1] == key]
            assert row and all(y0 < ax.get_position().y0 and ax.get_position().y1 < y1 for ax in row), (width, key)
            (bar,) = [ln for ln in fig.lines if ln.get_gid() == f"nw-rare-block-role:{key}"]
            expected = {"exceedance": Colors.accent, "hc": Colors.text_secondary, "shape": Colors.text_secondary,
                        "pooled": Colors.warn}[key]
            assert bar.get_color() == expected, (width, key)
        ordered = [spans[k] for k, *_rest in rx.TEST_ROWS]
        assert all(upper[0] > lower[1] for upper, lower in zip(ordered, ordered[1:])), width   # apart, in order
    (edge,) = [ln for ln in fig.lines if ln.get_gid() == "nw-rare-block-edge:pooled"]
    repaint_theme_ink(fig, "#000000", "#123456")
    assert edge.get_color() == "#123456"

def _groups_table(emptied):
    """Two control wells and two wells each of drug and drug2, ten nanowells a well; the groups in *emptied* have no
    value of the metric anywhere."""
    rows = []
    for well, condition in (("A01", "ctrl"), ("A02", "ctrl"), ("B01", "drug"), ("B02", "drug"),
                            ("C01", "drug2"), ("C02", "drug2")):
        for q in range(10):
            value = np.nan if condition in emptied else 1.0 + q + (5.0 if condition != "ctrl" and q < 2 else 0.0)
            rows.append({"plate_id": "p", "well_id": well, "condition": condition, "nanowell_id": f"{q},0",
                         "value": value})
    return pd.DataFrame(rows)


def test_every_treatment_group_excluded_is_named_not_drawn_as_an_empty_figure():
    """Reported: on a 12-timepoint plate the Rare responders figure showed only its top label and "Full report,
    warnings and notes: Details ▸ under the figure" — no headline, no panel. Every treatment nanowell had been
    excluded while the control's were kept, so the analysis returned no unit and no word of why."""
    from review.features.nanowell.rare_data_compute import VALUE_MISSING

    with pytest.raises(ValueError) as caught:
        rr.analyse(_groups_table({"drug", "drug2"}), rr.RareParams(control="ctrl"))
    assert str(caught.value) == (
        "no treatment group has a nanowell left to test: "
        f"drug: every one of its 20 nanowells was excluded before testing ({VALUE_MISSING}: 20); "
        f"drug2: every one of its 20 nanowells was excluded before testing ({VALUE_MISSING}: 20)")


def test_a_treatment_group_the_exclusions_emptied_is_warned_of_while_the_others_are_tested():
    from review.features.nanowell.rare_data_compute import VALUE_MISSING

    result = rr.analyse(_groups_table({"drug2"}), rr.RareParams(control="ctrl"))
    assert [u.treatment for u in result.units] == ["drug"]
    assert (f"drug2: every one of its 20 nanowells was excluded before testing ({VALUE_MISSING}: 20)"
            in result.warnings)


def test_a_well_whose_top_nanowells_are_past_every_control_is_marked_as_having_no_hc_score():
    """Found while drawing the page: Higher Criticism+ leaves out p ≤ 1/n, and an empirical p of a nanowell past every
    control value is 1/(N+1) — below 1/n whenever the control pool is larger than the well. A well whose most extreme
    nanowells are all past the controls, the strongest responders there are, has no HC score. User decision: keep the
    definition and mark the well on the figure, never leave it silently undrawn. Fifteen of a hundred nanowells past
    300 control ones: the top ten (alpha0·n) are all at p = 1/301 < 1/100."""
    res = rr.analyse(plate(n_ctrl=3, n_treat=3, n=100, responders=15, seed=2))
    unit = res.units[0]
    assert {unit.hc[w.key].reason for w in unit.treated} == {hc.BEYOND_CONTROLS}
    assert all(math.isnan(unit.hc[w.key].value) for w in unit.treated)
    fig = _laid_out(res, 14.5)
    shared = _panel(fig, "nw-rare-shared:hc")
    marks = {t.get_gid(): t.get_text() for t in shared.texts if str(t.get_gid()).startswith("rare-hc-peak-missing:")}
    assert marks == {f"rare-hc-peak-missing:{w.key}": "no HC score" for w in unit.treated}
    title = _panel(fig, "nw-rare-panel:hc:0").get_title().replace("\n", " ")
    assert title.startswith("colour: drug wells · grey: control no HC score: ")
    for w in unit.treated:
        assert f"{w.key.split(':', 1)[-1]} (its most extreme nanowells are past every control value" in title
    # A well that simply has nothing extreme is not blamed on the controls.
    assert hc.unscored_reason(np.array([0.5, 0.6, 0.9, 1.0]), 0.5).startswith("no HC score: none of its 2 most")


def test_what_qualifies_the_whole_result_is_drawn_at_the_top_not_below_the_fold():
    """Found retaking the tutorial: the figure scrolls, and the lead — a control taken because of its name, nanowells
    the run's flags left out — was drawn after all four test rows, where only a reader who scrolled past the whole
    figure would see it. It is drawn under the groups line, above the first comparison's headline and every panel."""
    res = rr.analyse(_small_plate(spiked=True), rr.RareParams(control="ctrl"))
    lead = "⚠ ctrl was taken as the control because of its name"
    fig = _laid_out(res, 14.5, ["a line"], lead=[lead], note="pointer")
    texts = {t.get_text(): t.get_position()[1] for t in fig.texts}
    groups = next(t.get_position()[1] for t in fig.texts if t.get_gid() == "nw-rare-groups")
    first_head = max(t.get_position()[1] for t in fig.texts if t.get_gid() == "nw-rare-unit")
    panels = [ax for ax in fig.axes if str(ax.get_gid()).startswith(("nw-rare-shared:", "nw-rare-panel:"))]
    assert groups > texts[lead] > first_head > max(ax.get_position().y1 for ax in panels)
    assert texts["a line"] < min(ax.get_position().y0 for ax in panels)       # the rest stays under the rows


def test_one_comparison_fills_half_the_row_and_nothing_on_it_overlaps():
    """Seen retaking the tutorial on a plate with one comparison: its curve panel took a third of the row and left the
    rest empty; the pooled row's caption sat on its p line, which has no shared panel between them; and a negative HC
    peak was drawn half off its axes. One comparison now takes half the row, every caption clears its row's p line, and
    every dot is inside its axes."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    res = rr.analyse(_small_plate(spiked=True), rr.RareParams(control="ctrl"))
    fig = _laid_out(res, 12.6)
    for key in ("exceedance", "hc", "pooled"):
        assert _panel(fig, f"nw-rare-panel:{key}:0").get_position().width * 12.6 >= 0.4 * 12.6, key
    renderer = FigureCanvasAgg(fig).get_renderer()
    for key in ("exceedance", "hc", "pooled"):
        p_box = next(t for t in fig.texts if t.get_gid() == f"nw-rare-test-p:{key}").get_window_extent(renderer)
        caption = next(t for t in fig.texts if t.get_gid() == f"nw-rare-comparison:{key}:0")
        # Clear by a visible space, not merely not overlapping: touching was what the tutorial showed.
        assert p_box.y0 - caption.get_window_extent(renderer).y1 >= 6 / 72 * fig.dpi, key
    hc_ax = _panel(fig, "nw-rare-shared:hc")
    values = [res.units[0].hc[w.key].value for w in res.units[0].controls + res.units[0].treated]
    assert min(values) < 0                                       # the fixture has a negative peak to place
    low, high = hc_ax.get_ylim()
    radius = (high - low) * 0.03
    assert all(low + radius <= v <= high for v in values if math.isfinite(v))


def test_a_group_p_computed_without_an_unscored_well_names_the_wells_it_used():
    """User decision (option 1): the group statistic of Higher Criticism is the mean over the wells that have a score,
    which keeps its p valid but leaves out a well whose most extreme nanowells are all past every control value — the
    strongest responders. The p is kept and says which wells it came from, on the figure's row and caption, over the
    group on the shared panel, and in Details. Only drug2 is spiked, so it alone has no HC score."""
    t = plate(n_ctrl=3, n_treat=3, n=100, responders=0, seed=4)
    spiked = (t["well_id"] == "drug2") & (t["nanowell_id"].astype(int) < 15)
    t.loc[spiked, "nt"] = (t.loc[spiked, "n0"] * 2 ** 6.5).astype(int)
    res = rr.analyse(t)
    unit = res.units[0]
    hc_scores = {w.key.split(":", 1)[-1]: unit.hc[w.key].value for w in unit.treated}
    assert math.isnan(hc_scores["drug2"]) and math.isfinite(hc_scores["drug0"]) and math.isfinite(hc_scores["drug1"])
    from review.features.nanowell import rare_export_compute as rx

    p = rx._num(unit.hc_perm.p)
    said = rx.test_p(unit, "hc", res.prepared.params, 1)
    assert said.startswith(f"p = {p}") and said.endswith(" — from drug0, drug1; drug2 has no HC score"), said
    fig = _laid_out(res, 14.5)
    row = next(t.get_text() for t in fig.texts if t.get_gid() == "nw-rare-test-p:hc")
    assert row.endswith(" — from drug0, drug1; drug2 has no HC score")
    caption = next(t.get_text() for t in fig.texts if t.get_gid() == "nw-rare-comparison:hc:0").replace("\n", " ")
    assert caption.endswith(" — from drug0, drug1; drug2 has no HC score")
    groups = [t.get_text() for t in _panel(fig, "nw-rare-shared:hc").texts if t.get_gid() == "rare-hc-peak-group"]
    assert any("(drug0, drug1 only)" in " ".join(g.split()) for g in groups), groups
    details = [line for line in rx.report_lines(res) if line.strip().startswith("Higher Criticism: mean score")]
    assert details and details[0].endswith(" — from drug0, drug1; drug2 has no HC score")
    # Every well scored, or none: nothing to qualify.
    assert rx.scored_note(rr.analyse(plate(n_ctrl=3, n_treat=3, n=100, seed=4)).units[0], "hc") == ""


def _drawn_overlaps(fig) -> list:
    """Every overlap on a drawn rare page that a reader sees: text running under a block's role bar or off the
    figure, text crossing a block's top or bottom edge, and text on text. Tick labels outside their axis' limits are
    not drawn and are skipped; so is the overlap of two slanted tick labels' upright bounding boxes, which a reader
    does not see."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from review.features.nanowell import _rare_render as rd

    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    renderer = canvas.get_renderer()

    def ticks(ax):
        out = []
        for labels, values, lim in ((ax.get_xticklabels(), ax.get_xticks(), ax.get_xlim()),
                                    (ax.get_yticklabels(), ax.get_yticks(), ax.get_ylim())):
            lo, hi = sorted(lim)
            out += [t for t, v in zip(labels, values) if lo - 1e-9 <= v <= hi + 1e-9 and t.get_text().strip()]
        return out

    texts = [t for t in fig.texts if t.get_text().strip()]
    for ax in fig.axes:
        if ax.get_gid() == rd.PAGE_MARK:
            continue
        texts += [t for t in (ax.title, ax.xaxis.label, ax.yaxis.label, *ax.texts) if t.get_text().strip()]
        texts += ticks(ax)
    boxes = [(t, t.get_window_extent(renderer)) for t in texts]
    bars = [ln.get_window_extent(renderer) for ln in fig.lines if str(ln.get_gid()).startswith("nw-rare-block-role")]
    fills = [p.get_window_extent(renderer) for p in fig.patches if str(p.get_gid()).startswith("nw-rare-block:")]
    width = fig.bbox.width
    found = []
    for t, b in boxes:
        name = t.get_text()[:40].replace("\n", " / ")
        if b.x0 < -0.5 or b.x1 > width + 0.5:
            found.append(("off the figure", name))
        if any(b.x0 < bar.x1 + 2 and b.y0 < bar.y1 and b.y1 > bar.y0 for bar in bars):
            found.append(("under a role bar", name))
        if any(b.x1 > f.x0 and b.x0 < f.x1 and (b.y0 < f.y1 < b.y1 or b.y0 < f.y0 < b.y1) for f in fills):
            found.append(("across a block's edge", name))
    slanted = {id(t) for t, _b in boxes if t.get_rotation() not in (0.0, 90.0, 270.0)}
    for i, (t, b) in enumerate(boxes):
        for u, c in boxes[i + 1:]:
            if id(t) in slanted and id(u) in slanted:
                continue
            # Touching counts: two texts less than 2 px apart read as one (the well names "A01A02A03", an x label's
            # second line on the next caption).
            if min(b.x1, c.x1) - max(b.x0, c.x0) > -2 and min(b.y1, c.y1) - max(b.y0, c.y0) > -2:
                found.append(("text on text", t.get_text()[:30], u.get_text()[:30]))
    return found


@pytest.mark.parametrize("n_drugs", [1, 3, 4])
def test_no_text_overlaps_a_role_bar_a_block_edge_or_other_text_at_any_width(n_drugs):
    """Reported: the colour bar overlapped the text, and the grey blocks the text around them. On a phone's figure
    (3.88 in) the margins were shares of the width, so text started 0.08 in from the edge — under the role bar,
    which ends 0.11 in in — and every block's heading, p line and the panels' y labels ran under it; titles wider than
    their panel ran off the figure; with many wells, count labels and well names ran into one another. Measured on the
    drawn figure, at a desk's, a narrow window's and a phone's width, with one to four comparisons, after the page's
    own passes: its 9 pt sizes, its X tick angle (which laid the upright well names flat into one another until the
    renderer recorded their angle) and, on the phone, its label fit (which broke an x label onto a second line that
    sat on the next comparison's caption)."""
    t = [plate(n=120, responders=4, seed=0, treatment="drug0")]
    t += [plate(n_ctrl=0, n=120, responders=4, seed=s, treatment=f"drug{s}") for s in range(1, n_drugs)]
    res = rr.analyse(pd.concat(t))
    for width in (12.58, 7.0, 3.88):
        fig = _laid_out(res, width, ["a line"], lead=["⚠ a lead line"], note="a note")
        # The page's style pass draws every tick label, axis label and title at 9 pt (Properties' sizes, measured on
        # the page), larger than matplotlib's defaults: a margin that clears the role bar at the defaults ran the
        # page's y labels under it on a phone.
        from review.core import plot_style

        for ax in fig.axes:
            ax.tick_params(labelsize=9)
            for label in (ax.xaxis.label, ax.yaxis.label, ax.title):
                label.set_fontsize(9)
            plot_style._style_x_tick_angle(ax, 0)     # the page's X tick angle at its default: "the plot's own"
        if width < 5:
            # And on a phone it breaks each label to fit its axes (``plot_style._fit_labels_to_phone``), which stands a
            # y label two lines wide — the shape that ran under the role bar.
            from matplotlib.backends.backend_agg import FigureCanvasAgg

            FigureCanvasAgg(fig).draw()
            plot_style._fit_labels_to_phone(fig)
        assert _drawn_overlaps(fig) == [], width


def test_a_shared_panel_too_narrow_for_every_well_wraps_its_comparisons_onto_another():
    """With six control wells and four comparisons of three wells each, a phone's shared panel would give each well
    about 8 pt — less than an upright 9 pt label — and the well names ran into one another. The panel wraps as the
    curve panels do: as many comparisons as fit with every well's slot at least ``MIN_SLOT_PT``, the rest on another
    panel below, the control wells drawn again on each, and every comparison on exactly one."""
    from review.features.nanowell import _rare_render as rd

    t = [plate(n=120, responders=4, seed=0, treatment="drug0")]
    t += [plate(n_ctrl=0, n=120, responders=4, seed=s, treatment=f"drug{s}") for s in range(1, 4)]
    res = rr.analyse(pd.concat(t))
    desk = _laid_out(res, 12.58)
    assert [ax.get_gid() for ax in desk.axes if str(ax.get_gid()).startswith("nw-rare-shared:exceedance")] == [
        "nw-rare-shared:exceedance"]                                             # the desk holds them all
    phone = _laid_out(res, 3.88)
    panels = [ax for ax in phone.axes if str(ax.get_gid()).startswith("nw-rare-shared:exceedance")]
    assert len(panels) > 1
    drawn = []
    for ax in panels:
        controls = [c for c in ax.collections if c.get_gid() == "rare-exceed-control"]
        assert controls and len(controls[0].get_offsets()) == 6                # every control well, on every panel
        groups = [t.get_text().split("\n")[0] for t in ax.texts if t.get_gid() == "rare-exceed-group"]
        drawn += groups[1:]                                                    # after the control's own name
        slot_pt = ax.get_position().width * 3.88 * 72 / (ax.get_xlim()[1] - ax.get_xlim()[0])
        assert slot_pt >= rd.MIN_SLOT_PT, slot_pt
    assert sorted(drawn) == sorted(u.label for u in res.units)                  # each comparison exactly once


def test_a_panel_keeps_room_under_it_for_every_line_its_x_label_is_broken_onto():
    """Seen on a phone: the page's label fit breaks an x label wider than its panel onto a second line
    (``plot_style._fit_labels_to_phone``), and that line sat on the next comparison's caption. Under each
    per-comparison panel, the room down to the next caption — or to its block's bottom edge — holds the tick labels
    and every line of the label as broken, at the page's 9 pt and matplotlib's tick and label spacing, with 2 pt to
    spare."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from review.core import plot_style

    t = [plate(n=120, responders=4, seed=0, treatment="drug0")]
    t += [plate(n_ctrl=0, n=120, responders=4, seed=s, treatment=f"drug{s}") for s in range(1, 3)]
    res = rr.analyse(pd.concat(t))
    fig = _laid_out(res, 3.88)
    for ax in fig.axes:
        ax.tick_params(labelsize=9)
        for label in (ax.xaxis.label, ax.yaxis.label, ax.title):
            label.set_fontsize(9)
    FigureCanvasAgg(fig).draw()
    plot_style._fit_labels_to_phone(fig)
    height_pt = fig.get_figheight() * 72
    checked = 0
    for ax in fig.axes:
        gid = str(ax.get_gid())
        if not gid.startswith("nw-rare-panel:"):
            continue
        key, i = gid.split(":")[1], int(gid.split(":")[2])
        lines = ax.xaxis.label.get_text().count("\n") + 1
        tick = ax.xaxis.get_major_ticks()[0]
        need = (tick.tick1line.get_markersize() + tick.get_pad() + 9 * 1.2 + ax.xaxis.labelpad
                + lines * 9 * ax.xaxis.label._linespacing + 2)
        below = [t.get_position()[1] for t in fig.texts if t.get_gid() == f"nw-rare-comparison:{key}:{i + 1}"]
        floor = below[0] if below else next(p.get_y() for p in fig.patches if p.get_gid() == f"nw-rare-block:{key}")
        room = (ax.get_position().y0 - floor) * height_pt
        assert room >= need, (gid, lines, round(room, 1), round(need, 1))
        checked += lines > 1
    assert checked                          # the fixture's x label is broken onto two lines on this phone's panels
