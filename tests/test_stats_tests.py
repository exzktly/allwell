"""The five statistical tests of workstream H, driven as pure functions.

Everything in ``domain/stats_tests.py`` takes lists of floats and returns a
dataclass, so all of it is fast-lane work — no app, no Qt, no plate. What the
Statistics tab adds on top is resolving the samples and formatting the report,
and that is tested separately.

Two properties get more attention than the arithmetic, because they are the
ones a plausible wrong implementation satisfies:

* **Undefined is not zero.** Every path that cannot produce a number returns a
  *reason* and leaves the number ``None``. A test that only checked "it did not
  raise" would pass against a function returning ``0.0`` for an interval that
  never converged, which is the exact failure §7 is about.
* **Pairing is by key, never by list order.** The fixtures below deliberately
  order the two groups' keys differently, so an implementation that zipped the
  lists computes a different number and the test can see it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from well_viewer.domain import stats_tests as st


# ── H1: effect size and interval ────────────────────────────────────────────

def test_hedges_g_applies_the_small_sample_correction():
    """Not Cohen's *d*. At n=3 per group the correction factor is 0.8, so the
    uncorrected statistic overstates the effect by 25%, and n=3 wells is the
    normal case in this app — so "the two differ" is not the assertion; the
    corrected value is.

    The wrong implementation this excludes is plain *d*, which is what the
    formula reduces to if the correction factor is dropped.
    """
    a = [10.0, 11.0, 12.0]
    b = [13.0, 14.0, 15.0]

    g = st.hedges_g(a, b)

    # d = (11 - 14) / 1.0 = -3.0 exactly; correction = 1 - 3/(4*4 - 1) = 0.8
    assert g == pytest.approx(-3.0 * (1.0 - 3.0 / 15.0))
    assert abs(g) < 3.0, "the uncorrected d came through"


def test_two_constant_groups_have_no_effect_size_rather_than_an_enormous_one():
    """A pooled SD of zero makes the ratio undefined. Returning a huge number
    would report the strongest possible effect for the one input that carries
    no information about spread at all."""
    assert st.hedges_g([5.0, 5.0, 5.0], [9.0, 9.0, 9.0]) is None


def test_the_interval_is_reported_undefined_rather_than_as_zero():
    result = st.effect_size([1.0], [2.0])

    assert result.undefined
    assert result.lo is None and result.hi is None
    assert result.g is None and result.diff is None
    assert not result.ok


def test_the_interval_brackets_the_difference_it_is_an_interval_for():
    a = [10.0, 10.5, 11.0, 10.2]
    b = [14.0, 14.4, 13.6, 14.1]

    result = st.effect_size(a, b)

    assert result.ok
    assert result.diff == pytest.approx(np.mean(a) - np.mean(b))
    assert result.lo <= result.diff <= result.hi
    assert result.hi < 0.0, "the interval should exclude no difference here"


def test_the_bootstrap_gives_the_same_interval_every_time():
    """``compute_pairwise`` promises a saved plot re-runs to the same brackets.
    An interval seeded from entropy would break that quietly, in the direction
    of looking like a real change in the data."""
    a = [3.0, 4.5, 2.2, 5.1, 3.8]
    b = [7.1, 6.4, 8.0, 6.9, 7.7]

    first = st.effect_size(a, b)
    second = st.effect_size(a, b)

    assert (first.lo, first.hi) == (second.lo, second.hi)


# ── H2: the omnibus gate ────────────────────────────────────────────────────

def test_the_omnibus_needs_three_groups_and_says_so():
    result = st.omnibus([("a", [1.0, 2.0]), ("b", [3.0, 4.0])])

    assert not result.ok
    assert "3 or more groups" in result.undefined
    assert result.p is None


def test_the_parametric_choice_selects_the_test_and_is_named():
    groups = [("a", [1.0, 2.0, 3.0]), ("b", [4.0, 5.0, 6.0]),
              ("c", [7.0, 8.0, 9.0])]

    anova = st.omnibus(groups, parametric=True)
    kruskal = st.omnibus(groups, parametric=False)

    assert anova.name == "One-way ANOVA" and anova.stat_name == "F"
    assert kruskal.name == "Kruskal-Wallis" and kruskal.stat_name == "H"
    assert anova.p != kruskal.p, "both branches returned the same test"


def test_the_omnibus_reports_its_degrees_of_freedom():
    """A reader quoting an F needs its two df, and printing the statistic
    without them is the shape that gets quoted wrong in a paper."""
    result = st.omnibus([("a", [1.0, 2.0, 3.0]), ("b", [4.0, 5.0, 6.0]),
                         ("c", [7.0, 8.0, 9.0, 10.0])])

    assert (result.df_between, result.df_within) == (2, 7)


def test_every_group_constant_is_undefined_and_not_a_p_of_nan():
    result = st.omnibus([("a", [2.0, 2.0]), ("b", [2.0, 2.0]), ("c", [2.0, 2.0])])

    assert not result.ok
    assert result.p is None


# ── H3: Dunnett ─────────────────────────────────────────────────────────────

def test_dunnett_compares_every_treatment_to_the_control_and_not_to_itself():
    result = st.dunnett_vs_control(
        "DMSO", [10.0, 10.4, 9.8],
        [("1 uM", [12.0, 12.5, 11.8]), ("10 uM", [18.0, 17.4, 18.6])])

    assert result.ok
    assert [c.name for c in result.comparisons] == ["1 uM", "10 uM"]
    assert "DMSO" not in [c.name for c in result.comparisons]
    for c in result.comparisons:
        assert c.p is not None and 0.0 <= c.p <= 1.0


def test_dunnett_costs_what_a_multiplicity_correction_costs():
    """Its p must exceed the *same* comparison left uncorrected.

    The comparison has to be on the same footing to mean anything: Dunnett
    pools the error variance over every group, so it carries far more residual
    degrees of freedom than a two-sample t-test on the same pair, and that extra
    power can more than pay for the correction. Against a bare two-sample test
    Dunnett's p is often *lower*, which is a real property and not a missing
    correction — so this checks the adjusted p against the unadjusted p of its
    own statistic at its own df, where the only difference left is the
    multiplicity.

    The effects are deliberately moderate. An earlier version of this fixture
    produced statistics with p near 1e-10, where scipy's multivariate-t
    integration is at its own precision floor and the ordering does not hold —
    a fixture unlike any plate this app will see, failing for a reason that
    said nothing about the code.
    """
    import scipy.stats as _sp

    control = [10.0, 10.4, 9.8, 10.1]
    treatments = [("a", [10.9, 11.3, 10.6, 11.0]),
                  ("b", [11.2, 10.7, 11.4, 10.9]),
                  ("c", [10.6, 10.9, 10.3, 10.7])]

    result = st.dunnett_vs_control("ctrl", control, treatments)

    n_obs = len(control) + sum(len(v) for _n, v in treatments)
    df_pooled = n_obs - (len(treatments) + 1)
    for comp in result.comparisons:
        uncorrected = float(2 * _sp.t.sf(abs(comp.stat), df_pooled))
        assert comp.p > uncorrected, (
            f"{comp.name}: adjusted p {comp.p:.3g} is not above the "
            f"uncorrected {uncorrected:.3g} for the same statistic")


def test_a_control_with_one_replicate_is_declined_with_a_reason():
    result = st.dunnett_vs_control("ctrl", [10.0],
                                   [("a", [12.0, 12.5, 11.8])])

    assert not result.ok
    assert "control" in result.undefined
    assert result.comparisons == ()


# ── H4: paired, and the pairing itself ──────────────────────────────────────

def test_pairing_is_by_key_and_never_by_list_order():
    """The fixture is built so the two answers differ.

    Both groups hold the same three batches, but in *different insertion
    orders*. Zipping the values would pair batch 1 with batch 3 and produce a
    different statistic — so an implementation that ignored the keys fails here
    rather than passing on a fixture where the orders happen to agree.
    """
    a = {"batch1": 10.0, "batch2": 20.0, "batch3": 30.0}
    b = {"batch3": 33.0, "batch1": 11.0, "batch2": 22.0}

    xa, xb, unmatched = st.pair_by_key(a, b)

    assert list(zip(xa, xb)) == [(10.0, 11.0), (20.0, 22.0), (30.0, 33.0)]
    assert unmatched == []


def test_a_replicate_only_one_group_holds_is_named_rather_than_dropped():
    """Silently dropping it makes a paired test on fewer replicates look like
    one on all of them, and the reader has no way to notice."""
    a = {"b1": 1.0, "b2": 2.0, "b3": 3.0}
    b = {"b1": 1.5, "b2": 2.5, "b9": 9.0}

    _xa, _xb, unmatched = st.pair_by_key(a, b)

    assert unmatched == ["b3", "b9"]


def test_the_paired_test_reports_the_pairs_it_used_and_the_ones_it_could_not():
    a = {"b1": 10.0, "b2": 12.0, "b3": 11.0, "b4": 13.0}
    b = {"b1": 14.0, "b2": 15.0, "b3": 16.0, "b7": 2.0}

    result = st.paired_test(a, b)

    assert result.ok
    assert result.n_pairs == 3
    assert result.unmatched == ("b4", "b7")
    assert result.name == "Paired t-test" and result.stat_name == "t"


def test_the_non_parametric_branch_is_the_signed_rank_test():
    a = {f"b{i}": float(i) for i in range(1, 9)}
    b = {f"b{i}": float(i) + 2.0 for i in range(1, 9)}

    result = st.paired_test(a, b, parametric=False)

    assert result.name == "Wilcoxon signed-rank" and result.stat_name == "W"
    assert result.ok


def test_too_few_shared_replicates_is_undefined_with_the_reason():
    result = st.paired_test({"b1": 1.0}, {"b1": 2.0, "b2": 3.0})

    assert not result.ok
    assert result.p is None
    assert "pair" in result.undefined
    assert result.n_pairs == 1


def test_a_paired_test_differs_from_the_unpaired_one_on_the_same_numbers():
    """If these agreed, the pairing is not reaching the test — which is the
    whole point of H4: replicate sets repeated across days are currently tested
    as independent, which is less powerful and slightly wrong."""
    import scipy.stats as _sp

    # Batch-to-batch spread is large and the within-batch shift is small and
    # consistent — the design a paired test exists for. The differences must
    # *vary*: a perfectly constant shift has no within-pair variance at all and
    # is declined, which the test below pins.
    a = {f"b{i}": v for i, v in enumerate([10.0, 20.0, 30.0, 40.0])}
    b = {f"b{i}": v for i, v in enumerate([11.2, 21.6, 31.1, 41.4])}

    paired = st.paired_test(a, b)
    unpaired = float(_sp.ttest_ind(list(a.values()), list(b.values())).pvalue)

    assert paired.ok
    assert paired.p < unpaired
    assert paired.p < 0.01 and unpaired > 0.5


def test_a_perfectly_constant_shift_is_declined_rather_than_reported():
    """Every pair differing by exactly the same amount leaves no within-pair
    variance, so ``ttest_rel`` returns NaN. That is *undefined*, and it reaches
    the report as a reason rather than as a blank beside a t of infinity."""
    a = {f"b{i}": float(i * 10) for i in range(4)}
    b = {f"b{i}": float(i * 10 + 1) for i in range(4)}

    result = st.paired_test(a, b)

    assert not result.ok
    assert result.p is None
    assert result.n_pairs == 4


# ── H5: two-way ANOVA ───────────────────────────────────────────────────────

def _balanced_rows():
    """A balanced 3x2 with four replicates a cell and real effects in both."""
    rng = np.random.default_rng(7)
    rows, cells = [], {}
    for i, a in enumerate(["low", "mid", "high"]):
        for j, b in enumerate(["HeLa", "U2OS"]):
            vals = 10 + 3.0 * i + 1.5 * j + 0.8 * i * j + rng.normal(0, 1.0, 4)
            cells[(a, b)] = vals
            rows += [(a, b, float(v)) for v in vals]
    return rows, cells


def _closed_form(cells, levels_a, levels_b, n):
    """The textbook balanced sums of squares, derived independently.

    This is the point of the test: a second algorithm, not a second call to the
    first one. The shipped implementation solves a least-squares model and takes
    Type II sums of squares by model comparison; this computes the same
    quantities from cell and marginal means. On a balanced design they must
    agree exactly, and §7.3 #8 is why the check is not the author's own numbers
    compared against themselves.
    """
    allv = np.concatenate([cells[(a, b)] for a in levels_a for b in levels_b])
    gm = allv.mean()
    mean_a = {a: np.concatenate([cells[(a, b)] for b in levels_b]).mean()
              for a in levels_a}
    mean_b = {b: np.concatenate([cells[(a, b)] for a in levels_a]).mean()
              for b in levels_b}
    return {
        "ss_a": len(levels_b) * n * sum((mean_a[a] - gm) ** 2 for a in levels_a),
        "ss_b": len(levels_a) * n * sum((mean_b[b] - gm) ** 2 for b in levels_b),
        "ss_ab": n * sum((cells[(a, b)].mean() - mean_a[a] - mean_b[b] + gm) ** 2
                         for a in levels_a for b in levels_b),
        "ss_e": sum(((cells[(a, b)] - cells[(a, b)].mean()) ** 2).sum()
                    for a in levels_a for b in levels_b),
    }


def test_the_two_way_table_matches_an_independent_derivation():
    rows, cells = _balanced_rows()
    expected = _closed_form(cells, ["low", "mid", "high"], ["HeLa", "U2OS"], 4)

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Cell line")

    assert result.ok
    by_name = {t.name: t for t in result.terms}
    assert by_name["Dose"].ss == pytest.approx(expected["ss_a"])
    assert by_name["Cell line"].ss == pytest.approx(expected["ss_b"])
    assert by_name["Dose x Cell line"].ss == pytest.approx(expected["ss_ab"])
    assert result.residual_ss == pytest.approx(expected["ss_e"])
    assert result.residual_df == 24 - 6


def test_the_two_way_table_carries_the_right_degrees_of_freedom():
    rows, _cells = _balanced_rows()

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Cell line")

    assert [t.df for t in result.terms] == [2, 1, 2]


def test_both_main_effects_are_found_where_the_data_has_them():
    rows, _cells = _balanced_rows()

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Cell line")

    by_name = {t.name: t for t in result.terms}
    assert by_name["Dose"].p < 0.001
    assert by_name["Cell line"].p < 0.001


#: The unbalanced fixture below, and the Type II sums of squares
#: ``statsmodels.anova_lm(typ=2)`` reports for it. Hard-coded from that
#: reference run rather than recomputed here, so this test cannot agree with the
#: implementation by sharing its arithmetic.
#:
#: **The number is what separates Type II from Type III.** Swap-symmetry does
#: not: Type III is symmetric too (measured on this same fixture, 95.796 /
#: 6.550 in either order), so the order test below excludes Type I and nothing
#: else. On this design the three types give three different answers, and only
#: one of them is what the module claims to compute.
_UNBALANCED_COUNTS = {("low", "HeLa"): 5, ("low", "U2OS"): 2,
                      ("mid", "HeLa"): 3, ("mid", "U2OS"): 6,
                      ("high", "HeLa"): 4, ("high", "U2OS"): 3}
_TYPE_II_SS = {"Dose": 169.28987797143947, "Line": 4.652462291792863}
_TYPE_III_SS = {"Dose": 95.79621515091752, "Line": 6.550198968568208}


def _unbalanced_rows():
    rng = np.random.default_rng(3)
    rows = []
    for (a, b), k in _UNBALANCED_COUNTS.items():
        i = ["low", "mid", "high"].index(a)
        j = ["HeLa", "U2OS"].index(b)
        rows += [(a, b, float(10 + 3 * i + 1.5 * j + rng.normal(0, 1)))
                 for _ in range(k)]
    return rows


def test_the_sums_of_squares_are_type_two_and_not_type_three():
    result = st.two_way_anova(_unbalanced_rows(), factor_a="Dose",
                              factor_b="Line")

    by_name = {t.name: t.ss for t in result.terms}
    assert by_name["Dose"] == pytest.approx(_TYPE_II_SS["Dose"], rel=1e-9)
    assert by_name["Line"] == pytest.approx(_TYPE_II_SS["Line"], rel=1e-9)
    assert by_name["Dose"] != pytest.approx(_TYPE_III_SS["Dose"], rel=1e-3)
    assert by_name["Line"] != pytest.approx(_TYPE_III_SS["Line"], rel=1e-3)


def test_type_two_gives_the_same_answer_whichever_factor_is_named_first():
    """The property Type II is chosen for. A sequential (Type I) decomposition
    gives each main effect a different sum of squares depending on the entry
    order once the design is unbalanced, and plate data is rarely balanced."""
    rng = np.random.default_rng(3)
    counts = {("low", "HeLa"): 5, ("low", "U2OS"): 2, ("mid", "HeLa"): 3,
              ("mid", "U2OS"): 6, ("high", "HeLa"): 4, ("high", "U2OS"): 3}
    rows = []
    for (a, b), k in counts.items():
        i = ["low", "mid", "high"].index(a)
        j = ["HeLa", "U2OS"].index(b)
        rows += [(a, b, float(10 + 3 * i + 1.5 * j + rng.normal(0, 1)))
                 for _ in range(k)]

    forward = st.two_way_anova(rows, factor_a="Dose", factor_b="Line")
    swapped = st.two_way_anova([(b, a, v) for a, b, v in rows],
                               factor_a="Line", factor_b="Dose")

    fwd = {t.name: t.ss for t in forward.terms}
    swp = {t.name: t.ss for t in swapped.terms}
    assert fwd["Dose"] == pytest.approx(swp["Dose"])
    assert fwd["Line"] == pytest.approx(swp["Line"])


def test_an_empty_cell_refuses_the_interaction_and_names_it():
    """A combination nothing holds makes the interaction inestimable. Computing
    it anyway from a rank-deficient design reports a number the data does not
    support, and nothing on screen would say so."""
    rows = [("low", "HeLa", 1.0), ("low", "HeLa", 1.4),
            ("low", "U2OS", 2.0), ("low", "U2OS", 2.3),
            ("high", "HeLa", 3.0), ("high", "HeLa", 3.6)]

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Line")

    interaction = [t for t in result.terms if "x" in t.name][0]
    assert interaction.undefined
    assert interaction.f is None and interaction.p is None
    assert "high x U2OS" in interaction.undefined
    assert result.empty_cells == ("high x U2OS",)


def test_one_level_of_a_factor_is_declined_rather_than_tested():
    rows = [("only", "HeLa", 1.0), ("only", "HeLa", 2.0),
            ("only", "U2OS", 3.0), ("only", "U2OS", 4.0)]

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Line")

    assert not result.ok
    assert "2 or more values" in result.undefined


def test_a_single_replicate_per_cell_has_nothing_to_test_against():
    rows = [("low", "HeLa", 1.0), ("low", "U2OS", 2.0),
            ("high", "HeLa", 3.0), ("high", "U2OS", 4.5)]

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Line")

    assert not result.ok
    assert "residual degrees of freedom" in result.undefined


# ── the cross-cutting rule ──────────────────────────────────────────────────

@pytest.mark.parametrize("result", [
    st.effect_size([1.0], [2.0]),
    st.omnibus([("a", [1.0, 2.0])]),
    st.dunnett_vs_control("c", [1.0], [("a", [1.0, 2.0])]),
    st.paired_test({"b1": 1.0}, {"b1": 2.0}),
    st.two_way_anova([("a", "x", 1.0)]),
])
def test_every_declined_result_says_why_and_carries_no_number(result):
    """The cross-cutting rule for all five: undefined is distinct from computed
    as zero, in the panel and in any CSV column. A result that came back with a
    number and no reason — or a reason and a number — would be the conflation
    §7 names as the cardinal sin."""
    assert result.undefined, f"{type(result).__name__} declined without a reason"
    assert not result.ok
    for field_name in ("p", "lo", "hi", "g", "stat", "f"):
        value = getattr(result, field_name, None)
        assert value is None or not math.isfinite(float(value)), (
            f"{type(result).__name__}.{field_name} carries {value!r} "
            "on a result that could not be computed")


# ── what the review found ───────────────────────────────────────────────────

def test_two_confounded_factors_are_refused_rather_than_reported_as_null():
    """The cardinal sin, in the shape that ships on a real plate.

    When each level of one variable appears with exactly one level of the
    other, the two are the same partition of the plate under two names and
    neither main effect is estimable. Least squares still solves the design,
    the model comparisons collapse to zero, and the table reported
    ``F = 0, p = 1`` for both — *neither factor matters* — on data whose levels
    differ by nine residual standard deviations.

    The wrong implementation this excludes is the one that ships: it produced a
    complete-looking table, so "it returned terms" or "it did not raise" passes
    against it. Only refusing does not.
    """
    rows = [("a1", "b1", 1.0), ("a1", "b1", 2.0),
            ("a2", "b2", 10.0), ("a2", "b2", 11.0)]

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Line")

    assert not result.ok
    assert "confounded" in result.undefined
    assert result.terms == ()


def test_a_partly_confounded_design_that_is_still_estimable_is_not_refused():
    """The other side: an empty cell alone does not confound the factors, and
    refusing there would throw away two perfectly good main effects."""
    rows = [("low", "HeLa", 1.0), ("low", "HeLa", 1.4),
            ("low", "U2OS", 2.0), ("low", "U2OS", 2.3),
            ("high", "HeLa", 3.0), ("high", "HeLa", 3.6)]

    result = st.two_way_anova(rows, factor_a="Dose", factor_b="Line")

    assert result.ok
    assert [t.name for t in result.terms] == ["Dose", "Line", "Dose x Line"]


def test_groups_constant_at_different_values_are_a_real_result():
    """``kruskal`` answers this perfectly well (H = 8.0, p = 0.018), and
    count-like statistics reach it routinely. The guard used to refuse it and
    print "no variance to partition" over a significant between-group effect."""
    result = st.omnibus([("A", [1.0, 1.0, 1.0]), ("B", [2.0, 2.0, 2.0]),
                         ("C", [3.0, 3.0, 3.0])], parametric=False)

    assert result.ok
    assert result.p == pytest.approx(0.018315638888734137)


def test_groups_all_constant_at_the_same_value_are_still_refused():
    result = st.omnibus([("A", [2.0, 2.0]), ("B", [2.0, 2.0]),
                         ("C", [2.0, 2.0])])

    assert not result.ok
    assert "same constant value" in result.undefined


def test_a_group_too_short_to_test_is_named_rather_than_dropped():
    result = st.omnibus([("a", [1.0, 2.0, 3.0]), ("b", [4.0, 5.0, 6.0]),
                         ("c", [7.0, 8.0, 9.0]), ("solo", [1.0])])

    assert result.ok
    assert result.dropped == ("solo",)


def test_a_treatment_too_short_for_dunnett_gets_a_row_saying_so():
    """It used to vanish from the table entirely, which is indistinguishable
    from a group the user never defined. The guard meant to notice could not
    fire — an empty ``usable`` had already returned above it."""
    result = st.dunnett_vs_control("ctrl", [1.0, 2.0, 3.0],
                                   [("T1", [4.0, 5.0, 6.0]), ("T2", [99.0])])

    assert [c.name for c in result.comparisons] == ["T1", "T2"]
    short = result.comparisons[1]
    assert short.p is None
    assert "fewer than 2 usable replicates" in short.undefined


def test_a_constant_paired_shift_says_which_degenerate_case_it_hit():
    """"the test returned no finite result" tells a user nothing they can act
    on, and this is the commonest way a paired test fails."""
    a = {f"b{i}": float(i * 10) for i in range(4)}
    b = {f"b{i}": float(i * 10 + 1) for i in range(4)}

    result = st.paired_test(a, b)

    assert not result.ok
    assert "same amount" in result.undefined


def test_an_interval_asked_for_one_resample_says_one_not_zero():
    result = st.effect_size([1.0, 2.0, 3.0], [4.0, 5.0, 6.0], resamples=1)

    assert "2 or more resamples, not 1" in result.undefined
