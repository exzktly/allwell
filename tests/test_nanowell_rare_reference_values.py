"""The exceedance test's numbers against a worked example, and what the report says when they are less sure.

The example is four wells a group of 1,000 nanowells each: control ``7, 9, 14, 11``; molecule A ``21, 24, 28, 23``;
B ``9, 10, 64, 13``; C ``13, 15, 16, 14``. Its reference values come from outside the code under test: the
closed-form binomial log-likelihood (the limit the beta-binomial fit must reach when the wells of a group agree as
closely as counting allows), and a brute-force enumeration of the 70 relabelings of eight wells.

Each test here failed against the code before the change it pins (CLAUDE.md §7.3 #2): the optimiser stopped up to
0.05 short of the maximum, a ρ estimated at zero was printed as ``1e-06``, a profile-interval end on a flat
likelihood came back as ``3.5e+08``, and nothing said that the p rests on a large-sample approximation.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from scipy import stats

from review.features.nanowell import rare_exceedance_compute as ex
from review.features.nanowell import rare_export_compute as rx
from review.features.nanowell import rare_report_compute as rr
from review.features.nanowell.rare_null_compute import Well, permutation_test

N = 1000
CONTROL = [7, 9, 14, 11]
MOLECULES = {"A": [21, 24, 28, 23], "B": [9, 10, 64, 13], "C": [13, 15, 16, 14]}


def _counts(treated, controls=CONTROL):
    rows = [ex.WellCount(f"c{i}", "P", "control", False, k, N, {}, {}) for i, k in enumerate(controls)]
    return rows + [ex.WellCount(f"t{i}", "P", "drug", True, k, N, {}, {}) for i, k in enumerate(treated)]


def _binomial_loglik(groups) -> float:
    """The log-likelihood with each group at its own pooled rate and no spread between wells."""
    return float(sum(stats.binom.logpmf(k, N, sum(ks) / (N * len(ks))) for ks in groups for k in ks))


def _wells(treated, controls=CONTROL):
    """Wells whose nanowell values are 1 for the first ``k`` and 0 for the rest: against a fixed cutoff of 0.5, each
    well's count is its ``k``."""
    def well(key, cond, k):
        v = np.sort(np.r_[np.ones(k), np.zeros(N - k)])
        return Well(key, "P", cond, {"1": v}, {"1": np.array([f"{key}-{i}" for i in range(N)])})
    return ([well(f"c{i}", "control", k) for i, k in enumerate(controls)],
            [well(f"t{i}", "drug", k) for i, k in enumerate(treated)])


def test_the_fit_reaches_the_binomial_limit_when_the_wells_of_each_group_agree():
    """C's wells and the control wells each vary less than counting alone would make them, so the best ρ is zero and
    the best log-likelihood is the binomial's. The fit must get there: stopping 0.02 short moved the likelihood-ratio
    statistic from 2.97 to 2.93."""
    k = np.array(CONTROL + MOLECULES["C"], float)
    X = np.column_stack([np.ones(8), [0] * 4 + [1] * 4])
    full = ex.fit_bb(k, np.full(8, float(N)), X)
    null = ex.fit_bb(k, np.full(8, float(N)), X, fixed={1: 0.0})
    assert full.loglik == pytest.approx(_binomial_loglik([CONTROL, MOLECULES["C"]]), abs=0.005)
    assert null.loglik == pytest.approx(_binomial_loglik([CONTROL + MOLECULES["C"]]), abs=0.005)


@pytest.mark.parametrize("molecule, lrt, z, p", [("A", 14.156, 3.762, 8.4e-5), ("C", 2.967, 1.722, 0.0425),
                                                  ("B", 0.825, 0.908, 0.182)])
def test_the_worked_example_s_statistic_z_and_p(molecule, lrt, z, p):
    """A and C against the binomial limits (their best ρ is zero in the fit with a gap; A's fit without one has
    ρ = 0.0025, taken from a multi-start search), B against the same search."""
    result = ex.beta_binomial_test(_counts(MOLECULES[molecule]), 99.0, ci=False)
    assert result.lrt == pytest.approx(lrt, abs=0.01)
    assert result.z == pytest.approx(z, abs=0.005)
    assert result.p == pytest.approx(p, rel=0.03)


def test_swapping_the_two_groups_changes_the_sign_and_nothing_else():
    """The likelihood-ratio statistic does not know which group is called the treatment."""
    forward = ex.beta_binomial_test(_counts(MOLECULES["A"]), 99.0, ci=False)
    swapped = ex.beta_binomial_test(_counts(CONTROL, controls=MOLECULES["A"]), 99.0, ci=False)
    assert swapped.lrt == pytest.approx(forward.lrt, abs=0.01)
    assert swapped.z == pytest.approx(-forward.z, abs=0.005)
    assert swapped.p == pytest.approx(1.0 - forward.p, abs=1e-4)


def test_a_spread_estimated_at_zero_is_said_as_zero_and_a_real_one_is_printed():
    """A's wells agree as closely as counting allows; B's do not (one well at 64 among 9, 10, 13)."""
    a = ex.beta_binomial_test(_counts(MOLECULES["A"]), 99.0, ci=False)
    b = ex.beta_binomial_test(_counts(MOLECULES["B"]), 99.0, ci=False)
    assert a.rho_zero is True
    assert b.rho_zero is False
    assert b.rho == pytest.approx(0.0079, abs=0.0005)
    assert a.rho_null == pytest.approx(0.0025, abs=0.0003)
    assert rx._rho_text({"rho": a.rho, "rho_at_zero": a.rho_zero}) == (
        "ρ ≈ 0 (the wells of each group agree as closely as counting alone allows)")
    assert rx._rho_text({"rho": b.rho, "rho_at_zero": b.rho_zero}) == f"ρ {b.rho:.3g}"


def test_an_interval_end_on_a_flat_likelihood_is_unbounded_not_a_large_number():
    """No control nanowell is past the cutoff: the odds ratio has no upper limit, and the interval says ∞."""
    result = ex.beta_binomial_test(_counts([3, 2, 4, 1], controls=[0, 0, 0, 0]), 99.0)
    assert math.isinf(result.ci[1])
    assert 1.0 < result.ci[0] < 1e3


@pytest.mark.parametrize("molecule", ["A", "B"])
def test_the_relabeling_p_of_the_mean_share_past_the_cutoff(molecule):
    """Against every way of calling four of the eight wells the treatment: the share of relabelings whose mean count
    is at least the observed one. A's four wells are the four highest, so only the observed labeling qualifies."""
    counts = CONTROL + MOLECULES[molecule]
    observed = sum(MOLECULES[molecule])
    expected = sum(sum(counts[i] for i in c) >= observed for c in itertools.combinations(range(8), 4)) / 70
    controls, treated = _wells(MOLECULES[molecule])
    perm = permutation_test(controls + treated, {w.key for w in treated}, ex.rate_statistic(99.0, 0.5), 10000, 0)
    assert perm.exhaustive and perm.n_labelings == 70
    assert perm.p == pytest.approx(expected)
    if molecule == "A":
        assert perm.p == pytest.approx(1 / 70)


def _unit(molecule):
    controls, treated = _wells(MOLECULES[molecule])
    return rr._run_unit("all", "drug", controls, treated, rr.RareParams(control="control", fixed_threshold=0.5))


def test_with_few_wells_the_report_says_the_p_is_approximate_and_gives_the_relabeling_p():
    """Four and four wells: the caveat beside the verdict names the well counts, says the p tends to be too small,
    and carries the p that rests on the wells alone."""
    unit = _unit("A")
    params = rr.RareParams(control="control", fixed_threshold=0.5)
    assert unit.primary.perm is not None and unit.primary.perm.p == pytest.approx(1 / 70)
    assert ("With 4 control and 4 drug wells, the exceedance test's p is a large-sample approximation: the spread "
            "between wells was estimated at zero, so it treats nanowells as independent, and it tends to come out too "
            "small, most of all when it is very small. Relabeling whole wells gives p = 0.0143 (lowest possible "
            "0.0143).") in rx.caveats(unit, params)


def test_the_caveat_does_not_claim_a_zero_spread_where_one_was_estimated():
    unit = _unit("B")
    text = " ".join(rx.caveats(unit, rr.RareParams(control="control", fixed_threshold=0.5)))
    assert "the exceedance test's p is a large-sample approximation: it tends to come out too small" in text
    assert "estimated at zero" not in text
