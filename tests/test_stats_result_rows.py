"""The Statistics results as table rows (design language row D15): each cell says what the text report's line
says — the same p spelling, stars and effect-size text — and a pair that could not be computed says why."""
from __future__ import annotations

from review.domain.significance import p_phrase
from review.features.stats._result_rows import group_rows, pair_rows


def test_a_group_row_is_its_name_n_mean_median_and_sd():
    assert group_rows([("Ctrl", [1.0, 2.0, 6.0]), ("Empty", [])]) == [
        ("Ctrl", "3", "3", "2", "2.646")]


def test_one_value_has_no_sd_and_says_so_rather_than_zero():
    assert group_rows([("Solo", [4.0])]) == [("Solo", "1", "4", "4", "—")]


def test_a_pair_row_carries_the_raw_and_adjusted_p_and_the_stars_of_the_adjusted_one():
    pairs = [("A", "B", "t", 2.5, 0.04, None), ("A", "C", "t", 0.4, 0.70, None)]
    adjusted = {0: 0.08, 1: 0.70}
    rows = pair_rows(pairs, adjusted, True)
    assert rows[0] == ("A vs B", "t = 2.5000", p_phrase(0.04), p_phrase(0.08, adjusted=True), "ns", "")
    assert rows[1][0] == "A vs C" and rows[1][4] == "ns"


def test_a_single_comparison_is_not_adjusted_and_its_stars_follow_the_raw_p():
    rows = pair_rows([("A", "B", "t", 5.0, 0.001, None)], {}, False)
    assert rows == [("A vs B", "t = 5.0000", p_phrase(0.001), "", "**", "")]


def test_a_pair_whose_test_raised_says_why_in_its_row():
    rows = pair_rows([("A", "B", "t", None, None, "too few values")], {}, False)
    assert rows == [("A vs B", "Error: too few values", "", "", "", "")]
