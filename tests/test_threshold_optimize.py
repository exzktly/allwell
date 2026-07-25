"""The two-population threshold optimizer core (``pipeline.threshold_optimize``).

Pure numpy in, numbers out — no Qt, no fixture, no pipeline run. This is the
shared objective the Analyze-side control-well pass and the Review-side
"Optimize ThreshFracOn" button both reduce to, so it is worth pinning directly
rather than only through either caller.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from pipeline.threshold_optimize import (
    MAX_SWEEP_STEPS,
    ThreshFracOptimizationResult,
    estimate_thresholds_from_controls,
    find_well_csv,
    fraction_on_for_threshold,
    optimize_between_populations,
    read_channel_values,
    relative_gap,
    sweep_bounds,
    sweep_thresholds,
)


# ── primitives ───────────────────────────────────────────────────────────────

def test_fraction_on_is_strictly_above():
    values = np.array([1.0, 2.0, 3.0])
    assert fraction_on_for_threshold(values, 2.0) == pytest.approx(1 / 3)
    # Strict ``>``: a value exactly at the threshold is OFF.
    assert fraction_on_for_threshold(values, 3.0) == 0.0
    assert fraction_on_for_threshold(values, 0.0) == 1.0


def test_fraction_on_of_nothing_is_nan():
    assert np.isnan(fraction_on_for_threshold(np.array([]), 1.0))


def test_relative_gap_normalises_by_the_larger_magnitude():
    fa = np.array([0.05])
    fb = np.array([0.005])
    # Absolute gap is only 0.045; relative gap is 0.9 — the whole point.
    assert relative_gap(fa, fb)[0] == pytest.approx(0.9)


def test_relative_gap_is_sign_safe():
    """A negative fraction (subtractive fold change) must not invert the curve.

    With a signed ``max(fa, fb)`` denominator this case yields a negative gap;
    the magnitude denominator keeps it positive and comparable.
    """
    gap = relative_gap(np.array([-0.4]), np.array([0.1]))
    assert gap[0] > 0


def test_relative_gap_coerces_undefined_entries_to_zero():
    gap = relative_gap(np.array([0.0, np.nan]), np.array([0.0, np.nan]))
    assert list(gap) == [0.0, 0.0]


# ── sweep validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("lo,hi,steps,match", [
    (float("nan"), 1.0, 10, "threshold range must be finite"),
    (5.0, 1.0, 10, "max threshold"),
    (0.0, 1.0, 1, "at least 2"),
    (0.0, 1.0, MAX_SWEEP_STEPS + 1, "or fewer"),
])
def test_sweep_thresholds_rejects_bad_input(lo, hi, steps, match):
    with pytest.raises(ValueError, match=match):
        sweep_thresholds(lo, hi, steps)


def test_sweep_bounds_clips_the_top_at_a_percentile():
    """One saturated cell must not stretch the grid past the useful region."""
    bulk = np.linspace(100.0, 200.0, 1000)
    outlier = np.array([1e6])
    _lo, hi = sweep_bounds(np.concatenate([bulk, outlier]))
    assert hi < 1e5


def test_sweep_bounds_starts_at_the_pooled_minimum():
    """Not 0.0 — a channel on a big background offset would waste half the grid."""
    lo, _hi = sweep_bounds(np.linspace(500.0, 900.0, 100))
    assert lo == pytest.approx(500.0)


def test_sweep_bounds_of_nothing_is_degenerate():
    assert sweep_bounds(np.array([])) == (0.0, 0.0)


def test_sweep_bounds_falls_back_to_max_when_the_percentile_collapses():
    """Mostly-constant data: the p99.5 equals the min, so use the true max."""
    values = np.concatenate([np.full(1000, 10.0), np.array([99.0])])
    lo, hi = sweep_bounds(values)
    assert lo == pytest.approx(10.0)
    assert hi == pytest.approx(99.0)


# ── the optimizer ────────────────────────────────────────────────────────────

def test_separated_populations_split_between_them():
    neg = np.full(200, 100.0)
    pos = np.full(200, 400.0)
    result = optimize_between_populations(pos, neg)
    assert isinstance(result, ThreshFracOptimizationResult)
    assert 100.0 <= result.threshold < 400.0
    assert result.frac_a == pytest.approx(1.0)
    assert result.frac_b == pytest.approx(0.0)
    assert result.difference == pytest.approx(1.0)
    assert (result.n_a, result.n_b) == (200, 200)


def test_ties_report_the_lowest_threshold():
    """Fraction-on is a step function, so a run of thresholds ties for the max
    gap. ``argmax`` takes the first, i.e. the lowest — pinned because callers
    round the result and a different tie rule shifts it."""
    neg = np.full(50, 0.0)
    pos = np.full(50, 100.0)
    result = optimize_between_populations(pos, neg, min_threshold=0.0,
                                          max_threshold=100.0, steps=101)
    assert result.threshold == pytest.approx(0.0)


def test_identical_populations_have_no_gap():
    same = np.linspace(0.0, 100.0, 200)
    assert optimize_between_populations(same, same.copy()).difference == 0.0


@pytest.mark.parametrize("a,b", [
    (np.array([]), np.linspace(0, 1, 10)),
    (np.linspace(0, 1, 10), np.array([])),
])
def test_empty_population_is_rejected(a, b):
    with pytest.raises(ValueError, match="no valid finite values"):
        optimize_between_populations(a, b)


def test_constant_pooled_population_is_rejected():
    """No spread to scan — the caller must skip the channel, not guess."""
    flat = np.full(100, 7.0)
    with pytest.raises(ValueError, match="degenerate"):
        optimize_between_populations(flat, flat.copy())


# ── the pipeline-side CSV layer ──────────────────────────────────────────────

def _write_csv(path, rows, *, header="gfp_mean_intensity"):
    lines = [header] + [str(v) for v in rows]
    path.write_text("\n".join(lines) + "\n")


def test_read_channel_values_reads_the_intensity_column(tmp_path):
    csv_path = tmp_path / "measurements_A01.csv"
    _write_csv(csv_path, [1.0, 2.0, 3.0])
    assert list(read_channel_values(csv_path, "gfp")) == [1.0, 2.0, 3.0]


def test_read_channel_values_drops_unparseable_and_non_finite(tmp_path):
    csv_path = tmp_path / "measurements_A01.csv"
    _write_csv(csv_path, [1.0, "", "abc", "nan", "inf", 2.0])
    assert list(read_channel_values(csv_path, "gfp")) == [1.0, 2.0]


def test_read_channel_values_honours_qc_and_crop_when_present(tmp_path):
    csv_path = tmp_path / "measurements_A01.csv"
    csv_path.write_text(
        "gfp_mean_intensity,QC_Pass,Crop_Pass\n"
        "1.0,1,1\n"
        "2.0,0,1\n"
        "3.0,1,False\n"
        "4.0,1,1\n"
    )
    assert list(read_channel_values(csv_path, "gfp")) == [1.0, 4.0]


@pytest.mark.parametrize("csv_name,channel", [
    ("measurements_A01.csv", "mcherry"),      # column absent
    ("nothing_here.csv", "gfp"),              # file absent
])
def test_read_channel_values_is_empty_when_it_cannot_read(tmp_path, csv_name, channel):
    path = tmp_path / "measurements_A01.csv"
    _write_csv(path, [1.0, 2.0])
    assert read_channel_values(tmp_path / csv_name, channel).size == 0


def test_find_well_csv_canonicalises_the_well_name(tmp_path):
    (tmp_path / "measurements_B03.csv").write_text("x\n")
    # The user typed "b3"; the file is "B03".
    assert find_well_csv(tmp_path, "measurements", "b3").name == "measurements_B03.csv"
    assert find_well_csv(tmp_path, "measurements", "B03").name == "measurements_B03.csv"
    assert find_well_csv(tmp_path, "measurements", "H12") is None


def test_estimate_from_controls_returns_one_threshold_per_channel(tmp_path):
    (tmp_path / "measurements_B02.csv").write_text(
        "gfp_mean_intensity,mcherry_mean_intensity\n"
        + "\n".join("400.0,900.0" for _ in range(50)) + "\n"
    )
    (tmp_path / "measurements_B11.csv").write_text(
        "gfp_mean_intensity,mcherry_mean_intensity\n"
        + "\n".join("100.0,200.0" for _ in range(50)) + "\n"
    )
    out = estimate_thresholds_from_controls(
        tmp_path, fluor_channels=["GFP", "mCherry"], csv_prefix="measurements",
        pos_well="B02", neg_well="B11", log=logging.getLogger("t"),
    )
    assert set(out) == {"gfp", "mcherry"}
    assert 100.0 <= out["gfp"] < 400.0
    assert 200.0 <= out["mcherry"] < 900.0
    # Rounded to an integer, matching the Otsu pass.
    assert all(v == round(v) for v in out.values())


def test_estimate_from_controls_skips_a_channel_it_cannot_use(tmp_path):
    (tmp_path / "measurements_B02.csv").write_text(
        "gfp_mean_intensity,dead_mean_intensity\n400.0,5.0\n400.0,5.0\n")
    (tmp_path / "measurements_B11.csv").write_text(
        "gfp_mean_intensity,dead_mean_intensity\n100.0,5.0\n100.0,5.0\n")
    out = estimate_thresholds_from_controls(
        tmp_path, fluor_channels=["GFP", "dead", "absent"],
        csv_prefix="measurements", pos_well="B02", neg_well="B11",
        log=logging.getLogger("t"),
    )
    # "dead" is constant across both wells and "absent" has no column; neither
    # falls back to a different objective, they are simply left out.
    assert set(out) == {"gfp"}


def test_estimate_from_controls_is_empty_when_a_control_csv_is_missing(tmp_path):
    (tmp_path / "measurements_B02.csv").write_text("gfp_mean_intensity\n1.0\n")
    assert estimate_thresholds_from_controls(
        tmp_path, fluor_channels=["GFP"], csv_prefix="measurements",
        pos_well="B02", neg_well="B11", log=logging.getLogger("t"),
    ) == {}


def test_estimate_from_controls_warns_when_the_wells_look_swapped(tmp_path, caplog):
    """The objective is symmetric, so it cannot detect the swap itself."""
    # "Positive" is the DIM well here.
    (tmp_path / "measurements_B02.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("100.0" for _ in range(50)) + "\n")
    (tmp_path / "measurements_B11.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("400.0" for _ in range(50)) + "\n")
    with caplog.at_level(logging.WARNING):
        estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="measurements",
            pos_well="B02", neg_well="B11", log=logging.getLogger("t"),
        )
    assert any("swapped" in r.getMessage().lower() for r in caplog.records)
