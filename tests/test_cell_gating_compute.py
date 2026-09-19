"""Unit tests for the extracted Cell Gating domain compute (audit A3).

``apply_fluor_gating`` was the per-well masking math buried inside
``GatingWorker`` (a QThread) — untestable without a GUI fixture. It now lives in
``cell_gating_compute`` and is exercised directly here. The optimizer scan core
also moved here; its behaviour is pinned by ``test_cell_gating_optimizer_fc`` and
smoke-checked below.
"""
from __future__ import annotations

import numpy as np
import pytest
import pandas as pd

from review.features.home.cell_gating.gating_compute import (
    ThreshFracOptimizationResult,
    apply_fluor_gating,
    fraction_on_for_threshold,
    scan_fc_fractions,
)


# ── apply_fluor_gating (intensity mode) ──────────────────────────────────────
def test_intensity_gate_marks_cells_above_floor():
    df = pd.DataFrame({"gfp_mean_intensity": [1.0, 6.0, 7.0]})
    inc = apply_fluor_gating(
        df, fluor_gates={"gfp": 5.0}, use_scatter=False, gating_gates=[],
        use_thresh=False, thresh_gates=[], ratios={})
    assert inc == 2
    assert list(df["Included"]) == [0, 1, 1]
    assert list(df["Gate_Pass"]) == [1, 1, 1]   # neutral in intensity mode
    assert list(df["Gate_On"]) == [1, 1, 1]     # neutral when thresholding off


def test_missing_channel_column_excludes_all():
    df = pd.DataFrame({"other": [1.0, 2.0]})
    inc = apply_fluor_gating(
        df, fluor_gates={"gfp": 5.0}, use_scatter=False, gating_gates=[],
        use_thresh=False, thresh_gates=[], ratios={})
    assert inc == 0
    assert list(df["Included"]) == [0, 0]


def test_non_numeric_values_are_not_included():
    df = pd.DataFrame({"gfp_mean_intensity": ["", "abc", "9"]})
    inc = apply_fluor_gating(
        df, fluor_gates={"gfp": 5.0}, use_scatter=False, gating_gates=[],
        use_thresh=False, thresh_gates=[], ratios={})
    assert inc == 1          # only the parseable "9" clears the floor
    assert list(df["Included"]) == [0, 0, 1]


def test_empty_frame_returns_zero_without_columns():
    df = pd.DataFrame({"gfp_mean_intensity": []})
    assert apply_fluor_gating(
        df, fluor_gates={"gfp": 5.0}, use_scatter=False, gating_gates=[],
        use_thresh=False, thresh_gates=[], ratios={}) == 0
    assert apply_fluor_gating(
        None, fluor_gates={}, use_scatter=False, gating_gates=[],
        use_thresh=False, thresh_gates=[], ratios={}) == 0


# ── numeric scan core ────────────────────────────────────────────────────────
def test_fraction_on_and_scan():
    vals = np.array([1.0, 2.0, 9.0, 10.0])
    assert fraction_on_for_threshold(vals, 5.0) == 0.5
    assert np.isnan(fraction_on_for_threshold(np.array([]), 5.0))

    th = np.linspace(0, 10, 6)
    fa, fb, diffs = scan_fc_fractions(th, vals, np.array([1.0, 1.5, 2.0, 2.5]))
    assert list(fa) == [1.0, 0.5, 0.5, 0.5, 0.5, 0.0]
    assert list(fb) == [1.0, 0.25, 0.0, 0.0, 0.0, 0.0]
    # relative gap is |fa-fb|/max(|fa|,|fb|); at th=0 both 1.0 → 0.0
    assert diffs[0] == 0.0
    # ...and at th=4, two of four on against none of four. The raw branch is
    # Laplace-smoothed, as the pipeline's objective is, so a perfect split at
    # n=4 scores (3/6 - 1/6) / (3/6) rather than a saturated 1.0 — which is the
    # whole point: without it, two cells on against none is indistinguishable
    # from 485 against none.
    assert diffs[2] == pytest.approx((3 / 6 - 1 / 6) / (3 / 6))


def test_result_dataclass_shape():
    r = ThreshFracOptimizationResult(
        threshold=3.0, frac_a=0.5, frac_b=0.0, difference=1.0, n_a=4, n_b=4)
    assert r.threshold == 3.0 and r.n_a == 4


# ── the optimizer's one-line readout ─────────────────────────────────────────
#
# Untested until now, which is how its relative-gap branch came to claim that
# "the midpoint of the run around the best is what was applied" for an answer
# that ``np.argmax`` had taken off the bottom edge of the run.

def _result(**over):
    from pipeline.threshold_optimize import ThreshFracOptimizationResult

    base = dict(threshold=250.0, frac_a=0.7, frac_b=0.01, difference=0.66,
                n_a=1000, n_b=1000, objective="supported-separation",
                k_a=700, k_b=10, youden=0.69, weight=1.0)
    base.update(over)
    return ThreshFracOptimizationResult(**base)


def test_the_readout_states_both_arms_as_counts_over_their_totals():
    from review.features.home.cell_gating.gating_compute import (
        format_optimizer_result,
    )

    line = format_optimizer_result(_result(), "A01", "H12")
    assert "threshold = 250" in line
    # The counts, not only the percentages: a reader cannot tell a gate resting
    # on thirty cells from one resting on sixty thousand from "70.0%".
    assert "A01 700/1,000 ON (70.0%)" in line
    assert "H12 10/1,000 ON (1.00%)" in line
    assert "separation ≥ 0.660 at 95% confidence" in line
    assert "Youden J = 0.690" in line
    # No weight is named when it is the default, and it is named when it is not.
    assert "weight" not in line
    assert "weight 3" in format_optimizer_result(
        _result(weight=3.0), "A01", "H12")


def test_the_readout_names_which_objective_produced_the_score():
    from review.features.home.cell_gating.gating_compute import (
        format_optimizer_result,
    )

    folded = format_optimizer_result(
        _result(objective="relative-gap", k_a=None, k_b=None, youden=None,
                difference=0.98), "A01", "H12")
    assert "relative gap 0.980 (fold-change scan)" in folded
    assert "confidence" not in folded
    # The count fields are None on that branch, so the line must not reach for
    # them — formatting them would raise, and inventing them would lie.
    assert "/1,000 ON" not in folded
    assert "adjusted fraction-on" in folded


def test_the_readout_says_which_point_of_a_tied_run_was_applied():
    """The two objectives take different points, and the line said "midpoint"
    for both. On the fold-change branch the value comes from ``np.argmax``,
    which is the run's bottom edge — 201.0 out of a run spanning 201 to 897,
    reported as though it were the 549 in the middle."""
    from review.features.home.cell_gating.gating_compute import (
        format_optimizer_result,
    )

    bounded = format_optimizer_result(
        _result(tie_count=233, tie_span=696.0), "A01", "H12")
    assert "233 cuts scored that same best" in bounded
    assert "a contiguous run spanning 696 intensity unit(s)" in bounded
    assert "the midpoint of the run around the best is what was applied" in bounded

    folded = format_optimizer_result(
        _result(objective="relative-gap", k_a=None, k_b=None, youden=None,
                tie_count=233, tie_span=696.0), "A01", "H12")
    assert "the lowest of them is what was applied" in folded
    assert "midpoint" not in folded

    scattered = format_optimizer_result(
        _result(tie_count=2, tie_span=696.0, tie_contiguous=False), "A01", "H12")
    assert "scattered, not adjacent on the grid, across" in scattered
    assert "contiguous" not in scattered


def test_the_readout_says_nothing_about_a_run_of_one():
    from review.features.home.cell_gating.gating_compute import (
        format_optimizer_result,
    )

    assert "scored that same best" not in format_optimizer_result(
        _result(tie_count=1), "A01", "H12")
