"""The shared histogram core (``review/domain/column_histogram.py``): shared edges, probabilities, one width scale.

The fixture is built so the three wrong implementations give different numbers from the right one:

* bins per sample — B alone would be binned on ``[1, 1.5, 2, 2.5, 3]`` and give ``[0.5, 0, 0, 0.5]``;
* each sample scaled to its own peak — B's half-widths would be ``[0.4, 0, 0.2, 0.2]``;
* counts rather than probabilities — A's would be ``[0.3, 0, 0, 0.1]``.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from review.domain import column_histogram as ch

A = [0.5, 0.5, 0.5, 3.5]
B = [1.0, 1.0, 1.2, 1.2, 2.6, 2.6, 3.0, 3.0]


def test_two_samples_share_edges_and_one_width_scale():
    h = ch.column_histograms([A, B], 4)
    assert h.edges.tolist() == [0.5, 1.25, 2.0, 2.75, 3.5]
    assert h.probs[0].tolist() == [0.75, 0.0, 0.0, 0.25]
    assert h.probs[1].tolist() == [0.5, 0.0, 0.25, 0.25]
    assert h.p_max == 0.75
    assert h.half_widths(0, 0.4) == pytest.approx([0.4, 0.0, 0.0, 0.4 / 3])
    assert h.half_widths(1, 0.4) == pytest.approx([0.8 / 3, 0.0, 0.4 / 3, 0.4 / 3])
    assert h.note() == "4 bins · widest bar = 75% of a column"
    assert h.n == (4, 8)


def test_bars_are_mirrored_about_the_column_and_skip_empty_bins():
    bottoms, heights, lefts, widths = ch.column_histograms([A, B], 4).bars(0, 2.0, 0.4)
    assert bottoms.tolist() == [0.5, 2.75]
    assert heights.tolist() == [0.75, 0.75]
    assert lefts == pytest.approx([1.6, 2.0 - 0.4 / 3])
    assert widths == pytest.approx([0.8, 0.8 / 3])


def test_every_drawn_sample_sums_to_one():
    rng = np.random.default_rng(0)
    h = ch.column_histograms([rng.normal(0, 1, 50), rng.normal(3, 2, 900)], 17)
    for p in h.probs:
        assert p.sum() == pytest.approx(1.0, abs=1e-12)


def test_an_integer_metric_bins_on_whole_numbers():
    h = ch.column_histograms([[0, 0, 1, 1, 1, 2], [2, 3, 3, 4, 7]], 4, integer=True)
    assert h.edges.tolist() == [-0.5, 1.5, 3.5, 5.5, 7.5]       # continuous edges would be [0, 1.75, 3.5, 5.25, 7]
    assert h.probs[0] == pytest.approx([5 / 6, 1 / 6, 0, 0])
    assert h.probs[1] == pytest.approx([0, 0.6, 0.2, 0.2])
    assert h.note() == "4 bins · widest bar = 83% of a column"


def test_a_small_integer_range_gets_one_bin_per_value_whatever_count_was_asked():
    h = ch.column_histograms([[0, 1, 2, 3, 3, 3]], 30, integer=True)
    assert h.edges.tolist() == [-0.5, 0.5, 1.5, 2.5, 3.5]
    assert h.note() == "4 bins (one per value) · widest bar = 50% of a column"


def test_log_bins_are_log_spaced_and_values_at_or_below_zero_are_stated_not_placed():
    h = ch.column_histograms([[-1, 0, 1, 10, 100]], 2, log=True)
    assert h.edges == pytest.approx([1, 10, 100])
    assert h.probs[0] == pytest.approx([1 / 5, 2 / 5])                # the two values ≤ 0 stay in the denominator
    assert h.off_axis == (2,)
    assert h.note() == "2 bins (log-spaced) · widest bar = 40% of a column · 2 values ≤ 0 not on the log axis"


def test_a_sample_too_small_for_a_histogram_does_not_set_the_width_scale():
    h = ch.column_histograms([A, [3.0]], 4)
    assert h.probs[1] is None and h.n[1] == 1
    assert h.p_max == 0.75                                         # with the single value counted it would be 1.0
    assert h.half_widths(0, 0.4)[0] == pytest.approx(0.4)
    assert not h.drawn(1)


def test_non_finite_values_are_dropped_before_counting():
    h = ch.column_histograms([[1.0, 2.0, 2.0, math.nan, math.inf]], 2)
    assert h.n == (3,)
    assert h.probs[0] == pytest.approx([1 / 3, 2 / 3])


def test_identical_values_make_one_padded_bin():
    h = ch.column_histograms([[5.0, 5.0, 5.0]], 10)
    assert h.edges == pytest.approx([4.75, 5.25])
    assert h.probs[0].tolist() == [1.0]
    assert ch.column_histograms([[2, 2, 2]], 10, integer=True).edges.tolist() == [1.5, 2.5]


def test_nothing_to_draw_gives_no_scale_and_no_note():
    h = ch.column_histograms([[], [1.0]], 10)
    assert math.isnan(h.p_max)
    assert h.note() == ""
    assert h.bars(0, 0.0, 0.4)[0].size == 0


@pytest.mark.parametrize("raw, kept", [(1, 2), (500, 200), (30.4, 30), (12, 12), (math.nan, 30), (True, 30),
                                       ("x", 30), (None, 30)])
def test_the_bin_count_is_clamped(raw, kept):
    assert ch.clamp_bins(raw) == kept
