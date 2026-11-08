"""The pure rules of the Bar tab's *Then relative to* (the ratio of ratios).

Fixtures are chosen so the plausible wrong rules give different numbers
(CLAUDE.md §7.8): FC(X) = 2.0 and FC(B) = 0.5 make the ratio 4, the difference
1.5 and swapped operands 0.25, and the two relative errors differ so absolute
and relative quadrature disagree.
"""
from __future__ import annotations

import math

import pytest

from review.domain import ratio_of_ratios as rr


def test_the_ratio_divides_the_bar_by_its_baseline_with_relative_errors():
    value, spread = rr.combine(2.0, 0.2, 0.5, 0.1, "ratio")
    assert value == pytest.approx(4.0)
    assert spread == pytest.approx(4.0 * math.hypot(0.2 / 2.0, 0.1 / 0.5))


def test_subtract_takes_the_difference_with_absolute_errors():
    value, spread = rr.combine(2.0, 0.2, 0.5, 0.1, "subtract")
    assert value == pytest.approx(1.5)
    assert spread == pytest.approx(math.hypot(0.2, 0.1))


def test_an_undefined_baseline_spread_leaves_the_result_s_spread_undefined():
    """Stage 1 would call it exact; a one-well baseline is not exact."""
    value, spread = rr.combine(2.0, 0.2, 0.5, float("nan"), "ratio")
    assert value == pytest.approx(4.0)
    assert math.isnan(spread)


def test_a_missing_baseline_value_gives_no_value():
    value, _spread = rr.combine(2.0, 0.2, float("nan"), 0.1, "ratio")
    assert math.isnan(value)


def test_the_interaction_has_four_cells():
    w = rr.interaction_weights({"A01"}, {"B01"}, {"C01"}, {"D01"})
    assert w == {frozenset({"A01"}): 1, frozenset({"B01"}): -1,
                 frozenset({"C01"}): -1, frozenset({"D01"}): 1}
    assert rr.contrast_label(w, log=True) == "interaction, log scale"


def test_a_bar_that_is_its_own_control_is_a_reference_condition():
    """WT·Drug: X = control(X) and B = control(B) — everything cancels."""
    w = rr.interaction_weights({"A01"}, {"A01"}, {"C01"}, {"C01"})
    assert rr.is_reference(w)


def test_a_bar_that_is_its_own_baseline_is_a_reference_condition():
    """Mut·Veh: X = B(X) and control(X) = control(B(X))."""
    w = rr.interaction_weights({"C01"}, {"D01"}, {"C01"}, {"D01"})
    assert rr.is_reference(w)


def test_one_control_for_every_bar_cancels_and_is_not_called_an_interaction():
    w = rr.interaction_weights({"A01"}, {"Z01"}, {"C01"}, {"Z01"})
    assert w == {frozenset({"A01"}): 1, frozenset({"C01"}): -1}
    assert rr.contrast_label(w, log=False).startswith("bar vs its baseline only")


def test_cells_are_wells_so_a_set_and_its_wells_cancel_regardless_of_order():
    w = rr.interaction_weights(["A02", "A01"], ["A01", "A02"], {"C01"}, {"C01"})
    assert rr.is_reference(w)


def test_the_suffix_names_the_baseline_and_the_operator():
    assert rr.suffix("Treatment", "Vehicle", "ratio") == " ÷ (Treatment = Vehicle)"
    assert rr.suffix("Treatment", "Vehicle", "subtract") == " − (Treatment = Vehicle)"
