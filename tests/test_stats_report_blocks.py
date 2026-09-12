"""The report blocks the five workstream-H tests write, as pure text.

``features/stats/_report.py`` takes a dataclass from ``domain/stats_tests`` and
returns lines. Nothing here builds a widget, so the wording of every block is
fast-lane work — which matters more than usual, because these lines *are* the
tab's output and a reader quotes them into a paper.

The property under most of these tests is the one a plausible wrong
implementation breaks: **a block that could not be computed prints its reason
where the number would have gone**. A blank, a dash, or a zero all read as a
result. Only the reason reads as an absence.
"""

from __future__ import annotations

from review.domain import stats_tests as st
from review.features.stats import _report as rep


def _text(lines) -> str:
    return "\n".join(lines)


# ── effect size ─────────────────────────────────────────────────────────────

def test_the_effect_line_carries_both_quantities():
    effect = st.effect_size([10.0, 10.5, 11.0, 10.2], [14.0, 14.4, 13.6, 14.1])

    text = rep.effect_size_text(effect)

    assert text.startswith("g = ")
    assert "Δ = " in text
    assert "95% CI" in text


def test_an_undeclined_effect_prints_the_reason_and_no_number():
    """The wrong implementation this excludes prints an empty string, which
    leaves the p-value alone on the line looking like a complete result."""
    text = rep.effect_size_text(st.effect_size([1.0], [2.0]))

    assert "fewer than 2 usable replicates" in text
    assert "g = " not in text and "Δ" not in text


def test_no_effect_object_at_all_prints_nothing():
    assert rep.effect_size_text(None) == ""


# ── omnibus ─────────────────────────────────────────────────────────────────

def test_the_omnibus_line_names_the_test_its_df_and_its_p():
    groups = [("a", [1.0, 2.0, 3.0]), ("b", [8.0, 9.0, 10.0]),
              ("c", [15.0, 16.0, 17.0])]

    text = _text(rep.omnibus_lines(st.omnibus(groups)))

    assert "One-way ANOVA" in text
    assert "F(2, 6) = " in text
    assert "p = " in text


def test_a_non_significant_omnibus_says_so_without_hiding_the_pairwise():
    """It must not suppress them — which comparisons a reader may look at is
    not this app's policy — but it must say what it found."""
    groups = [("a", [1.0, 2.0, 3.0]), ("b", [1.1, 2.1, 2.9]),
              ("c", [0.9, 2.2, 3.1])]

    text = _text(rep.omnibus_lines(st.omnibus(groups)))

    assert "No overall difference at the 5% level" in text
    assert "still shown" in text


def test_a_two_group_run_prints_no_omnibus_refusal_at_all():
    """The commonest path, and the one place a refusal would be noise.

    "an omnibus needs 3 or more groups" over every two-group comparison — and
    over every KS run, which is two groups by definition — refuses something
    the user never asked for. Silence is the honest answer there; every other
    shortfall is a real one and is said.
    """
    assert rep.omnibus_lines(st.omnibus([("a", [1.0, 2.0])])) == []
    assert rep.omnibus_lines(
        st.omnibus([("a", [1.0, 2.0]), ("b", [3.0, 4.0])])) == []


def test_a_real_omnibus_shortfall_is_still_said():
    """The other half. Silencing the two-group case must not silence the cases
    that mean something — a wrong implementation that returned ``[]`` for every
    refusal would pass the test above on its own."""
    text = _text(rep.omnibus_lines(st.omnibus(
        [("a", [2.0, 2.0]), ("b", [2.0, 2.0]), ("c", [2.0, 2.0])])))

    assert "not run" in text
    assert "same constant value" in text


def test_a_group_the_omnibus_left_out_is_named():
    """An omnibus over three of four groups reads exactly like one over all
    four, and the degrees of freedom will not tell a reader which."""
    text = _text(rep.omnibus_lines(st.omnibus(
        [("a", [1.0, 2.0, 3.0]), ("b", [4.0, 5.0, 6.0]),
         ("c", [7.0, 8.0, 9.0]), ("solo", [1.0])])))

    assert "Left out" in text
    assert "solo" in text


def test_the_rank_based_branch_is_named_in_the_report():
    """A run whose pairwise tests are rank-based must not be gated by an ANOVA,
    and the report is where a reader checks which one it was."""
    groups = [("a", [1.0, 2.0, 3.0]), ("b", [8.0, 9.0, 10.0]),
              ("c", [15.0, 16.0, 17.0])]

    text = _text(rep.omnibus_lines(st.omnibus(groups, parametric=False)))

    assert "Kruskal-Wallis" in text
    assert "One-way ANOVA" not in text


# ── Dunnett ─────────────────────────────────────────────────────────────────

def test_the_dunnett_block_names_the_control_on_every_comparison():
    result = st.dunnett_vs_control(
        "DMSO", [10.0, 10.4, 9.8, 10.1],
        [("1 uM", [10.9, 11.3, 10.6, 11.0]),
         ("10 uM", [11.4, 11.9, 11.2, 11.6])])

    text = _text(rep.dunnett_lines(result))

    assert "1 uM  vs  DMSO" in text
    assert "10 uM  vs  DMSO" in text
    assert "DMSO  vs  DMSO" not in text


def test_a_dunnett_that_could_not_run_says_why_and_prints_no_table():
    result = st.dunnett_vs_control("ctrl", [1.0], [("a", [1.0, 2.0])])

    text = _text(rep.dunnett_lines(result))

    assert "not run" in text
    assert "vs" not in text.split("not run")[1]


# ── paired ──────────────────────────────────────────────────────────────────

def test_the_paired_block_reports_the_pair_count():
    a = {"b1": 10.0, "b2": 12.0, "b3": 11.5}
    b = {"b1": 14.0, "b2": 15.2, "b3": 16.0}

    text = _text(rep.paired_lines("A", "B", st.paired_test(a, b)))

    assert "n = 3 pairs" in text


def test_an_unpaired_replicate_is_named_in_the_report():
    """Counted away, a paired test on three of five replicates reads exactly
    like one on all five. The reader has nothing else to notice it by."""
    a = {"b1": 10.0, "b2": 12.0, "b3": 11.5, "b4": 9.0}
    b = {"b1": 14.0, "b2": 15.2, "b3": 16.0, "b9": 3.0}

    text = _text(rep.paired_lines("A", "B", st.paired_test(a, b)))

    assert "Unpaired: 2 replicate(s)" in text
    assert "b4" in text and "b9" in text


def test_a_paired_test_that_could_not_pair_says_so_rather_than_printing_a_p():
    text = _text(rep.paired_lines("A", "B", st.paired_test({"b1": 1.0}, {"b1": 2.0})))

    assert "Not computed" in text
    assert "p = " not in text
    # The count goes on the declined line too: "nothing could be paired" and
    # "the pairs carry no variation" are different problems with different
    # fixes, and this is what separates them.
    assert "n = 1 pairs" in text


# ── two-way ─────────────────────────────────────────────────────────────────

def _two_way():
    import numpy as np
    rng = np.random.default_rng(4)
    rows = []
    for i, a in enumerate(["low", "high"]):
        for j, b in enumerate(["HeLa", "U2OS"]):
            rows += [(a, b, float(10 + 4 * i + 2 * j + rng.normal(0, 0.6)))
                     for _ in range(4)]
    return st.two_way_anova(rows, factor_a="Dose", factor_b="Line")


def test_the_two_way_table_has_a_row_per_term_and_a_residual():
    text = _text(rep.two_way_lines(_two_way()))

    assert "Type II sums of squares" in text
    for term in ("Dose", "Line", "Dose x Line", "Residual"):
        assert any(ln.startswith(term) for ln in text.splitlines()), term


def test_an_inestimable_interaction_is_printed_as_not_estimable_not_as_zero():
    """A rank-deficient interaction reported as ``0.0000`` is a number nothing
    in the data supports, and the table gives a reader no way to tell."""
    rows = [("low", "HeLa", 1.0), ("low", "HeLa", 1.4),
            ("low", "U2OS", 2.0), ("low", "U2OS", 2.3),
            ("high", "HeLa", 3.0), ("high", "HeLa", 3.6)]

    text = _text(rep.two_way_lines(
        st.two_way_anova(rows, factor_a="Dose", factor_b="Line")))

    assert "not estimable" in text
    assert "high x U2OS" in text
    interaction = [ln for ln in text.splitlines() if ln.startswith("Dose x Line")][0]
    assert "0.0000" not in interaction and "0.000" not in interaction


def test_a_two_way_that_could_not_run_says_why():
    text = _text(rep.two_way_lines(st.two_way_anova([("a", "x", 1.0)])))

    assert "not run" in text
    assert "fewer than 4 usable replicates" in text


# ── the partial effect size ─────────────────────────────────────────────────

def test_a_partial_effect_prints_both_the_numbers_and_the_missing_half():
    """Two constant groups have a real difference and a real (zero-width)
    interval and no pooled SD. Printing the interval and dropping the reason
    leaves a reader with a zero-width CI, no ``g``, and no explanation of
    either — which is the conflation this module exists to prevent, in its
    quietest form."""
    text = rep.effect_size_text(st.effect_size([1.0, 1.0, 1.0], [2.0, 2.0, 2.0]))

    assert "Δ = -1" in text
    assert "no g:" in text
    assert "pooled SD" in text
    assert "g = " not in text.split("(no g")[0]


def test_the_interval_label_states_the_level_it_was_computed_at():
    """Hard-coded, it read ``95% CI`` over an interval computed at any level."""
    text = rep.effect_size_text(
        st.effect_size([1.0, 2.0, 3.0, 4.0], [6.0, 7.0, 8.0, 9.0], level=0.90))

    assert "90% CI" in text
    assert "95%" not in text
