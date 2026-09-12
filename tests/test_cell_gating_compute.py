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
