"""The statistics are valid at the replicate counts the app treats as normal.

Three findings of the full-app review (``_Docs/Markdowns/FULL_REVIEW_TRACKER.md``, rows S1-S3), each held
against a reference this file computes independently of the code under test:

* **S1** — a pair whose test could not be computed is not a member of the Benjamini-Hochberg family.
* **S2** — a rank-sum test on a handful of wells is exact, with or without ties; three against three can
  never give a two-sided p below 0.1.
* **S3** — the interval printed as a 95% CI covers the true difference about 95% of the time at n = 3.
"""
from __future__ import annotations

import itertools
import math
import warnings

import numpy as np
import pytest
import scipy.stats as st

from review.domain import stats_tests as T
from review.features.stats import controller as C


# ── S2 ───────────────────────────────────────────────────────────────────────

def _brute_force_p(a, b) -> float:
    """Two-sided permutation p of the rank sum: every relabelling of the pooled values, counted in full."""
    pooled = list(a) + list(b)
    ranks = st.rankdata(pooled)
    n = len(a)
    expected = n * (len(pooled) + 1) / 2.0
    observed = abs(sum(ranks[:n]) - expected)
    hits = total = 0
    for idx in itertools.combinations(range(len(pooled)), n):
        total += 1
        hits += abs(sum(ranks[i] for i in idx) - expected) >= observed - 1e-9
    return hits / total


@pytest.mark.parametrize("a, b, expected", [
    ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0], 0.1),            # complete separation, 3 v 3: 2/20
    ([2.0, 2.0, 2.0], [5.0, 5.0, 5.0], 0.1),            # the same, every value tied within its group
    ([1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0], 2 / 70),
    ([1.0, 2.0], [3.0, 4.0], 2 / 6),
])
def test_a_rank_sum_p_is_exact_on_a_handful_of_replicates(a, b, expected):
    result = T.rank_sum_test(a, b)
    assert result.method == "exact"
    assert result.p == pytest.approx(expected, abs=1e-12)
    assert result.p == pytest.approx(_brute_force_p(a, b), abs=1e-12)


def test_ties_between_the_groups_are_enumerated_not_approximated():
    a, b = [3.0, 3.0, 4.0, 7.0], [3.0, 5.0, 7.0, 9.0, 9.0]
    result = T.rank_sum_test(a, b)
    assert result.method == "exact"
    assert result.p == pytest.approx(_brute_force_p(a, b), abs=1e-12)
    # U and W are the textbook quantities, so the report's statistic is checkable against scipy's.
    assert result.u == pytest.approx(float(st.mannwhitneyu(a, b).statistic))
    assert result.w == pytest.approx(result.u + len(a) * (len(a) + 1) / 2)


def test_without_ties_the_exact_p_is_scipys_exact_p():
    rng = np.random.default_rng(3)
    for na, nb in ((3, 4), (5, 5), (6, 8)):
        a, b = rng.normal(0, 1, na), rng.normal(0.8, 1, nb)
        assert T.rank_sum_test(a, b).p == pytest.approx(
            float(st.mannwhitneyu(a, b, alternative="two-sided", method="exact").pvalue), abs=1e-12)


def test_large_samples_fall_back_to_the_approximation_and_say_so():
    rng = np.random.default_rng(4)
    a, b = rng.normal(0, 1, 200), rng.normal(0.2, 1, 180)
    result = T.rank_sum_test(a, b)
    assert result.method == "normal approximation"
    assert result.p == pytest.approx(
        float(st.mannwhitneyu(a, b, alternative="two-sided", method="asymptotic").pvalue))


def test_identical_constants_have_no_ranks_to_compare():
    result = T.rank_sum_test([1.0, 1.0, 1.0], [1.0, 1.0, 1.0])
    assert not result.ok and result.p is None


@pytest.mark.parametrize("test", ["Wilcoxon rank-sum", "Mann-Whitney U"])
def test_the_statistics_tab_never_stars_three_wells_against_three(test):
    """Both names are one test, and neither may report what no exact rank test can."""
    groups = [("ctrl", [1.0, 2.0, 3.0]), ("drug", [4.0, 5.0, 6.0])]
    (_a, _b, stat_name, stat, p, err), = C._run_pair_tests(groups, test, False)
    assert err is None
    assert p == pytest.approx(0.1, abs=1e-12)
    assert (stat_name, stat) == (("W", 6.0) if test.startswith("Wilcoxon") else ("U", 0.0))


def test_the_nanowells_page_uses_the_same_exact_rank_test():
    from review.features.nanowell import statistics_compute as N

    test = next(t for t in N.TESTS if t["key"] == "mannwhitney")
    a = N.Sample("ctrl", ("A01",), np.array([2.0, 2.0, 2.0]))
    b = N.Sample("drug", ("B01",), np.array([5.0, 5.0, 5.0]))
    assert N.pair_test(a, b, test).p == pytest.approx(0.1, abs=1e-12)


# ── S1 ───────────────────────────────────────────────────────────────────────

def test_a_pair_that_could_not_be_tested_is_not_in_the_bh_family():
    """Two groups saturated at the same constant give scipy a NaN, with no exception."""
    groups = [("ctrl", [0.2, 0.3, 0.45]), ("drugA", [0.31, 0.5, 0.61]),
              ("satA", [1.0, 1.0, 1.0]), ("satB", [1.0, 1.0, 1.0])]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = C._run_pair_tests(groups, "t-test (Welch, unequal var)", False)
    by_pair = {(r[0], r[1]): r for r in results}
    untestable = by_pair[("satA", "satB")]
    assert untestable[4] is None and "no finite result" in untestable[5]

    adjusted = C._bh_adjust(results)
    index = {(r[0], r[1]): i for i, r in enumerate(results)}
    assert index[("satA", "satB")] not in adjusted
    # The reference: scipy's own BH over exactly the five pairs that have a p.
    finite = [(i, r[4]) for i, r in enumerate(results) if r[4] is not None]
    reference = st.false_discovery_control([p for _i, p in finite], method="bh")
    assert len(finite) == 5
    for (i, _p), want in zip(finite, reference):
        assert adjusted[i] == pytest.approx(float(want), rel=1e-12)


def test_one_computable_pair_is_not_called_adjusted():
    results = [("a", "b", "t", 2.0, 0.03, None), ("a", "c", "t", None, None, "x")]
    assert C._bh_adjust(results) == {}


# ── S3 ───────────────────────────────────────────────────────────────────────

def test_the_interval_on_three_replicates_is_welchs():
    a, b = [10.2, 11.9, 9.4], [14.1, 13.0, 16.2]
    effect = T.effect_size(a, b)
    reference = st.ttest_ind(a, b, equal_var=False).confidence_interval(0.95)
    assert effect.method == "t"
    assert (effect.lo, effect.hi) == pytest.approx((reference.low, reference.high), rel=1e-10)


def test_the_95_percent_interval_covers_the_truth_about_95_percent_of_the_time_at_n_3():
    """The percentile bootstrap this replaced covered 80%; anything under 92% here is that defect back."""
    rng = np.random.default_rng(11)
    true_diff, runs, covered = 1.5, 1500, 0
    for _ in range(runs):
        effect = T.effect_size(rng.normal(true_diff, 1.0, 3), rng.normal(0.0, 1.0, 3))
        covered += effect.lo <= true_diff <= effect.hi
    assert 0.93 <= covered / runs <= 0.985


def test_large_samples_keep_the_bootstrap_and_say_so():
    rng = np.random.default_rng(12)
    effect = T.effect_size(rng.normal(1, 1, 400), rng.normal(0, 1, 400))
    assert effect.method == "bootstrap"
    assert effect.lo < 1.0 < effect.hi


def test_two_constant_groups_keep_their_difference_and_a_zero_width_interval():
    effect = T.effect_size([1.0, 1.0], [2.0, 2.0])
    assert effect.ok and effect.g is None and effect.g_undefined
    assert (effect.diff, effect.lo, effect.hi) == (-1.0, -1.0, -1.0)
    assert math.isfinite(effect.level)
