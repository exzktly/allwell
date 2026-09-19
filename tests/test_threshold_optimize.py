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
    OBJECTIVE_SUPPORTED,
    ThreshFracOptimizationResult,
    candidate_thresholds,
    counts_above,
    plateau_pick,
    support_floor,
    supported_separation,
    wilson_bounds,
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


# ── the confidence-bounded objective's primitives ────────────────────────────

def test_wilson_keeps_a_width_where_wald_collapses_to_zero():
    """Why Wilson and not the textbook interval, in one assertion.

    ``p ± z·sqrt(p(1-p)/n)`` is exactly zero wide at ``k = 0`` — it reports that
    a threshold no negative-control cell crossed carries no uncertainty
    whatsoever, which is the arithmetic the old objective's tail-chasing was
    made of. Wilson inverts the score test instead and keeps the width the
    sample size actually supports.
    """
    lo, hi = wilson_bounds(np.array([0.0]), 100)
    assert lo[0] == pytest.approx(0.0, abs=1e-12)
    assert hi[0] == pytest.approx(0.0370, abs=5e-4)

    # Wider on less data, narrower on more — and never zero on either.
    _lo10, hi10 = wilson_bounds(np.array([0.0]), 10)
    _lo1e5, hi1e5 = wilson_bounds(np.array([0.0]), 100_000)
    assert hi10[0] > hi[0] > hi1e5[0] > 0.0

    # Symmetric at the top end: k == n is the same problem mirrored.
    lo_full, hi_full = wilson_bounds(np.array([100.0]), 100)
    assert hi_full[0] == 1.0
    assert lo_full[0] == pytest.approx(1.0 - hi[0], abs=1e-12)


def test_wilson_of_nothing_is_the_whole_interval():
    lo, hi = wilson_bounds(np.array([0.0]), 0)
    assert (lo[0], hi[0]) == (0.0, 1.0)


def test_the_score_falls_as_the_cells_behind_it_run_out():
    """A perfect ratio on ten cells must lose to a good one on ten thousand.

    The two rows below are the user-reported scenario and its correct answer:
    ten cells ON against none, versus sixty thousand against five thousand. The
    first is a flawless ratio and the second is not, and the first is worthless.
    """
    thin = supported_separation(np.array([10.0]), 100_000,
                                np.array([0.0]), 100_000)[0]
    thick = supported_separation(np.array([60_000.0]), 100_000,
                                 np.array([5_000.0]), 100_000)[0]
    assert thick > 0.5
    assert thin < 0.001
    assert thick > 100 * thin


def test_the_score_is_a_bound_and_so_can_be_negative():
    """Below zero means "no separation survives its own interval"."""
    # Two cells ON out of ten against one out of ten: J is +0.1 and the interval
    # around it is far wider than that.
    assert supported_separation(np.array([2.0]), 10, np.array([1.0]), 10)[0] < 0.0
    # The same proportions on a thousand cells each are a real difference.
    assert supported_separation(np.array([200.0]), 1000,
                                np.array([100.0]), 1000)[0] > 0.0


def test_the_weight_scales_the_estimate_and_its_uncertainty_together():
    """A cost that large on the error rate is that large on being wrong about it.

    Weighting only the point estimate would let a heavily-weighted false
    positive rate be penalised while the uncertainty in it stayed cheap.
    """
    k_neg = np.array([50.0])
    at_1 = supported_separation(np.array([900.0]), 1000, k_neg, 1000, weight=1.0)[0]
    at_5 = supported_separation(np.array([900.0]), 1000, k_neg, 1000, weight=5.0)[0]
    # 4 extra units of a 0.05 false-positive rate, plus 4 extra units of that
    # rate's own Wilson width — strictly more than the 0.20 the estimate alone
    # would have cost.
    assert at_1 - at_5 > 0.20


def test_the_candidate_grid_holds_one_point_per_distinguishable_cut():
    """One midpoint between each pair of neighbouring pooled values, and no more.

    Fraction-on is a step function, so two thresholds inside the same interval
    classify every cell identically and scanning both is scanning one twice. The
    grid is therefore the data's own, not a resolution anybody chose.
    """
    a = np.array([1.0, 3.0, 3.0])
    b = np.array([2.0, 7.0])
    grid = candidate_thresholds(a, b)
    assert list(grid) == [1.5, 2.5, 5.0]

    # Bounds clip it; duplicates collapse; no spread means nothing to scan.
    assert list(candidate_thresholds(a, b, lo=2.0, hi=4.0)) == [2.5]
    assert candidate_thresholds(np.full(50, 7.0)).size == 0

    # The cap subsamples rather than truncating, so the top of the range is
    # still reachable — truncating it would reintroduce the percentile clip the
    # old grid needed.
    dense = candidate_thresholds(np.arange(1000.0), max_candidates=11)
    assert dense.size == 11
    assert dense[0] == pytest.approx(0.5)
    assert dense[-1] == pytest.approx(998.5)


def test_counts_above_is_strictly_above():
    values = np.sort(np.array([1.0, 2.0, 2.0, 3.0]))
    assert list(counts_above(values, np.array([0.0, 2.0, 3.0, 9.0]))) == [4, 1, 0, 0]


def test_the_support_floor_gives_way_on_populations_too_small_for_it():
    # The requested floor, where the wells can spare it.
    assert support_floor(5000, 5000, 10) == 10
    # A tenth of the larger well, where they cannot — a floor of 10 against two
    # wells of 12 cells would reject every cut worth making.
    assert support_floor(12, 12, 10) == 1
    assert support_floor(50, 80, 10) == 8
    assert support_floor(0, 0, 10) == 0


# ── the optimizer ────────────────────────────────────────────────────────────

def test_separated_populations_split_between_them():
    neg = np.full(200, 100.0)
    pos = np.full(200, 400.0)
    result = optimize_between_populations(pos, neg)
    assert isinstance(result, ThreshFracOptimizationResult)
    # Not merely inside the gap: the gap holds exactly one distinguishable cut,
    # and the candidate grid is built so that cut is its midpoint.
    assert result.threshold == pytest.approx(250.0)
    assert result.frac_a == pytest.approx(1.0)
    assert result.frac_b == pytest.approx(0.0)
    assert (result.k_a, result.k_b) == (200, 0)
    assert result.youden == pytest.approx(1.0)
    # Just under 1.0, and by how much is the whole point: the score is a lower
    # confidence bound, so a perfect split of 200 against 200 pays the width of
    # the interval those counts support. 200 against 200 keeps 0.973; the same
    # perfect split on 5 cells against 5 keeps 0.386, and on one cell against
    # one it is refused outright.
    assert result.difference == pytest.approx(0.9733, abs=5e-4)
    assert result.objective == OBJECTIVE_SUPPORTED
    assert (result.n_a, result.n_b) == (200, 200)


def test_a_perfect_split_of_a_handful_of_cells_is_not_a_perfect_score():
    """Same separation, three sample sizes — the score has to tell them apart.

    ``|fa-fb| / max(|fa|, |fb|)`` returned exactly 1.0 for all three, which is
    the defect in one line: it has no N in it, so a gate resting on one cell and
    a gate resting on a thousand were the same answer and ``argmax``'s
    first-index tie-break chose between them arbitrarily.
    """
    # One cell against one is a perfect ratio and no evidence at all, so it is
    # not scored low — it is refused.
    with pytest.raises(ValueError, match="no threshold separates"):
        optimize_between_populations(np.full(1, 400.0), np.full(1, 100.0))

    scores = []
    for n in (5, 200, 5000):
        r = optimize_between_populations(np.full(n, 400.0), np.full(n, 100.0))
        assert r.frac_a == 1.0 and r.frac_b == 0.0   # identical separation
        scores.append(r.difference)
    assert scores == [pytest.approx(0.3855, abs=5e-4),
                      pytest.approx(0.9733, abs=5e-4),
                      pytest.approx(0.9989, abs=5e-4)]


def test_ties_report_the_middle_of_the_run_not_its_bottom_edge():
    """``argmax`` takes the first tied index, which is the worst of the equals.

    A run of tied thresholds is real — fraction-on is a step function — and its
    bottom edge is wherever the negative control's brightest cell happens to
    sit, the single least stable number in the data. ``plateau_pick`` reports
    the middle of the contiguous run instead.

    Driven through the picker rather than through a fitted pair of populations,
    because the exact candidate grid collapses most genuine plateaus to one
    point on its own: each candidate sits between two neighbouring pooled
    values, so crossing one always changes a count. This pins the rule for the
    cases that survive that — a subsampled grid, or two different count pairs
    that coincidentally score alike.
    """
    grid = np.linspace(0.0, 100.0, 11)
    scores = np.array([0.1, 0.2, 0.9, 0.9, 0.9, 0.9, 0.9, 0.3, 0.2, 0.1, 0.0])
    idx, threshold, tie_count, tie_span, contiguous = plateau_pick(grid, scores)
    assert tie_count == 5
    assert contiguous
    assert tie_span == pytest.approx(40.0)
    # The run is indices 2..6, i.e. thresholds 20..60; its middle is 40, where
    # ``argmax`` alone would have said 20.
    assert idx == 4
    assert threshold == pytest.approx(40.0)


def test_a_split_tie_reports_a_point_inside_one_of_its_runs():
    """Two separated maxima are not a plateau, and their midpoint scored lower.

    The reported point must come from the run the argmax is standing on, never
    from the empty ground between two peaks.
    """
    grid = np.linspace(0.0, 100.0, 11)
    scores = np.array([0.1, 0.9, 0.9, 0.2, 0.2, 0.2, 0.2, 0.9, 0.9, 0.1, 0.0])
    idx, threshold, tie_count, tie_span, contiguous = plateau_pick(grid, scores)
    assert tie_count == 4
    assert not contiguous
    assert tie_span == pytest.approx(70.0)
    assert scores[idx] == 0.9
    assert threshold in (pytest.approx(10.0), pytest.approx(20.0))


def test_identical_populations_are_refused_not_scored_zero():
    """Two draws from one distribution have no threshold, and it says so.

    This used to return a result with ``difference == 0`` — a number the caller
    then had to know to check — and on real (non-linspace) data it returned a
    confident-looking non-zero score instead, because a ratio of small counts
    finds a maximum in noise like anything else. The bound is signed, so
    "nothing beats zero" is a statement about the wells.
    """
    rng = np.random.default_rng(4)
    a = rng.lognormal(5.3, 0.4, 2000)
    b = rng.lognormal(5.3, 0.4, 2000)
    with pytest.raises(ValueError, match="no threshold separates"):
        optimize_between_populations(a, b)


def test_the_positive_arm_is_not_interchangeable_with_the_negative():
    """``TPR(a) - w*FPR(b)`` is signed, so the wells cannot be passed backwards.

    The objective this replaced was ``|fa - fb| / …`` — symmetric, so a swapped
    pair of control wells produced a confidently optimised threshold and a
    warning afterwards guessing that they might have been swapped. A guess is
    what an unusable objective leaves you with.
    """
    pos = np.full(200, 400.0)
    neg = np.full(200, 100.0)
    assert optimize_between_populations(pos, neg).threshold == pytest.approx(250.0)
    with pytest.raises(ValueError, match="no threshold separates"):
        optimize_between_populations(neg, pos)


def test_the_specificity_weight_buys_a_cleaner_negative_control():
    """Raising *w* trades sensitivity for a lower false-positive floor.

    The fixture is built so the two answers differ: Well B's cells sit at three
    levels, so every step up in *w* has somewhere cheaper to move the cut to.
    """
    rng = np.random.default_rng(8)
    pos = np.concatenate([rng.normal(1500, 200, 700), rng.normal(300, 80, 300)])
    neg = np.concatenate([rng.normal(300, 80, 900), rng.normal(900, 150, 100)])

    plain = optimize_between_populations(pos, neg, weight=1.0)
    strict = optimize_between_populations(pos, neg, weight=10.0)

    assert strict.threshold > plain.threshold
    assert strict.frac_b < plain.frac_b
    assert strict.frac_a <= plain.frac_a
    assert plain.weight == 1.0 and strict.weight == 10.0


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


def test_estimate_from_controls_refuses_swapped_control_wells(tmp_path, caplog):
    """A swapped pair is skipped, not thresholded and then second-guessed.

    The symmetric objective could not see the swap at all: it optimised the pair
    backwards, stored the threshold, and logged a question about whether the
    wells might be the wrong way round. The channel is now simply left out, and
    the reason is in the log.
    """
    # "Positive" is the DIM well here.
    (tmp_path / "measurements_B02.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("100.0" for _ in range(50)) + "\n")
    (tmp_path / "measurements_B11.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("400.0" for _ in range(50)) + "\n")
    with caplog.at_level(logging.WARNING):
        out = estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="measurements",
            pos_well="B02", neg_well="B11", log=logging.getLogger("t"),
        )
    assert out == {}
    assert any("no threshold separates" in r.getMessage() for r in caplog.records)


def test_estimate_from_controls_reports_both_arms_counts(tmp_path, caplog):
    """The run log must carry the N, since the N is what the estimate rests on.

    A line reporting only "GFP -> 250" and two percentages is the same line the
    old estimator wrote when it had chased a gate down to thirty cells.
    """
    (tmp_path / "measurements_B02.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("400.0" for _ in range(50)) + "\n")
    (tmp_path / "measurements_B11.csv").write_text(
        "gfp_mean_intensity\n" + "\n".join("100.0" for _ in range(50)) + "\n")
    with caplog.at_level(logging.INFO):
        estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="measurements",
            pos_well="B02", neg_well="B11", log=logging.getLogger("t"),
        )
    line = next(r.getMessage() for r in caplog.records if "250" in r.getMessage())
    assert "B02 50/50 ON" in line
    assert "B11 0/50 ON" in line
    assert "95% confidence" in line
