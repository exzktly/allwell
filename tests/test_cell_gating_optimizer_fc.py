"""Fold-change adjustment of the Cell Gating ThreshFracOn optimizer scan."""
from __future__ import annotations

import numpy as np

from pipeline.threshold_optimize import floor_unsupported, relative_gap
import pandas as pd

from review.features.home.cell_gating.gating_compute import (
    _optimize_thresh_frac_on_between_wells,
    scan_fc_fractions,
)


def _df(values):
    return pd.DataFrame({"area_px": [100] * len(values),
                         "Included": [1] * len(values),
                         "gfp_mean_intensity": values})


def test_scan_fc_off_matches_raw_fraction_gap():
    th = np.linspace(0, 10, 11)
    a = np.array([0, 1, 2, 8, 9, 10], float)
    b = np.array([0, 0, 0, 0, 0, 1], float)
    fa, fb, diffs = scan_fc_fractions(th, a, b)
    # raw fraction-on at threshold 0: A=5/6, B=1/6
    assert fa[0] == np.count_nonzero(a > 0) / a.size
    assert fb[0] == np.count_nonzero(b > 0) / b.size
    # gap is |fa-fb|/max(fa,fb), never NaN
    assert np.all(np.isfinite(diffs))


def test_scan_fc_vs_control_ratio_is_scale_invariant_where_control_on():
    # The relative-gap metric |fa-fb|/max(fa,fb) is invariant to dividing BOTH
    # wells by the same (shared) control fraction — so vs-control *ratio* leaves
    # the gap unchanged wherever the control still has signal, and zeroes it
    # where the control fraction is 0 (those become NaN -> coerced to 0). It
    # never introduces NaN/inf into argmax. (vs-baseline / subtract are what
    # actually reshape the metric — see the tests below.)
    th = np.linspace(0, 8, 9)
    a = np.array([5, 6, 7, 8, 9, 10], float)
    b = np.array([0, 1, 2, 3, 4, 5], float)
    ctrl = np.array([10, 10, 10, 10], float)  # frac-on = 1.0 across th in [0,8]
    fa_raw, fb_raw, _d_raw = scan_fc_fractions(th, a, b)
    _fa, _fb, diffs = scan_fc_fractions(th, a, b, ctrl_values=ctrl, op_ctrl="ratio")
    assert np.all(np.isfinite(diffs))
    # Compared against the *unsmoothed* gap of the same raw fractions, not
    # against `scan_fc_fractions`' own no-fold-change return. That branch is
    # Laplace-smoothed — its fractions are k/n and both n are known — while the
    # fold-changed branch is not, because `scale_bar_value`'s output is no
    # longer a count. Comparing the two would measure the smoothing, not the
    # scale invariance this test is named for.
    assert np.allclose(relative_gap(fa_raw, fb_raw), diffs)


def test_scan_fc_vs_baseline_reshapes_the_metric():
    # vs-t0 uses a DIFFERENT denominator per well, so it genuinely changes the
    # relative-gap curve (unlike the shared-control ratio above).
    th = np.linspace(0, 10, 11)
    a = np.array([5, 6, 7, 8, 9, 10], float)
    b = np.array([0, 1, 2, 3, 4, 5], float)
    base_a = np.array([8, 9, 10], float)
    base_b = np.array([0, 0, 1], float)
    _fa_raw, _fb_raw, d_raw = scan_fc_fractions(th, a, b)
    _fa, _fb, diffs = scan_fc_fractions(
        th, a, b, base_values_a=base_a, base_values_b=base_b, op_t0="ratio")
    assert np.all(np.isfinite(diffs))
    assert not np.allclose(d_raw, diffs)


def test_scan_fc_subtract_control_matches_scale_bar_value():
    from review.domain.fold_change import scale_bar_value
    th = np.array([0.0, 5.0])
    a = np.array([10.0, 10.0], float)  # frac-on = 1.0 at t=0, 0.5 at t=5
    b = np.array([0.0, 0.0], float)
    ctrl = np.array([10.0, 0.0], float)  # frac = 1.0 at every t>... actually >0 → 0.5
    fa, fb, _ = scan_fc_fractions(th, a, b, ctrl_values=ctrl, op_ctrl="subtract")
    # Cross-check the first threshold against scale_bar_value directly.
    fctrl0 = np.count_nonzero(ctrl > 0.0) / ctrl.size
    expect_a0, _ = scale_bar_value(1.0, 0.0, control_mean=fctrl0, op_ctrl="subtract")
    assert fa[0] == expect_a0


def test_optimizer_accepts_fc_kwargs_and_off_is_unchanged():
    a = _df([0, 10, 20, 30])
    b = _df([0, 0, 0, 0])
    base = dict(channel_key="gfp", min_threshold=0, max_threshold=30, steps=4)
    raw = _optimize_thresh_frac_on_between_wells(a, b, **base)
    # Passing empty FC kwargs must reproduce the raw result exactly.
    same = _optimize_thresh_frac_on_between_wells(
        a, b, **base, ctrl_values=None, base_values_a=None, base_values_b=None,
    )
    assert (same.threshold, same.frac_a, same.frac_b, same.difference) == (
        raw.threshold, raw.frac_a, raw.frac_b, raw.difference)


def test_scan_fc_subtract_control_metric_not_inverted():
    # Regression: under the *subtract* operator the adjusted fractions go
    # negative (wells below their control), and a signed max(fa, fb) denominator
    # used to zero the gap across that whole region (and spike at the sign
    # crossing) — inverting the curve / picking the wrong threshold. The
    # magnitude denominator keeps the metric finite, non-negative, and peaked
    # where A and B actually separate.
    th = np.array([0, 1, 2, 3, 4], dtype=float)
    a = np.array([5, 5, 5, 5], dtype=float)   # on across the whole range
    b = np.array([3, 3, 3, 3], dtype=float)   # drops off at t=3
    ctrl = np.array([10, 10, 10, 10], dtype=float)  # control on everywhere
    fa, fb, diffs = scan_fc_fractions(th, a, b, ctrl_values=ctrl, op_ctrl="subtract")
    assert np.all(np.isfinite(diffs)) and np.all(diffs >= 0)
    assert diffs.max() > 0                     # not crushed to all-zero
    assert int(np.argmax(diffs)) == 3          # separation peak, not index 0


def test_opt_curve_ylim_expands_for_out_of_range_curves():
    from review.features.home.cell_gating.gating_compute import _opt_curve_ylim

    # Raw fraction-on case keeps the familiar [0, 1] frame.
    lo, hi = _opt_curve_ylim(np.array([0.0, 0.5, 1.0]), np.array([0.0, 0.2]),
                             np.array([0.0, 0.8]))
    assert lo <= -0.02 and hi >= 1.02

    # Under subtraction the gap can exceed 1 (fa/fb straddle zero) and fractions
    # can go negative — the limits must cover them, not clip at ~1.
    lo, hi = _opt_curve_ylim(np.array([0.5, -0.5]), np.array([-0.5, 0.5]),
                             np.array([2.0, 0.0]))
    assert hi >= 2.0       # the >1 gap is visible
    assert lo <= -0.5      # negative adjusted fractions are visible
    # Non-finite values are ignored, never crashing or poisoning the range.
    lo, hi = _opt_curve_ylim(np.array([np.nan, np.inf, 0.3]))
    assert np.isfinite(lo) and np.isfinite(hi)


def test_the_fold_change_branch_keeps_the_relative_gap_and_says_so():
    """Its fractions are not ``k / n`` any more, so no confidence bound applies.

    ``scale_bar_value`` divides (or subtracts) each well's fraction-on by a
    control's at the same threshold, so the scanned quantity exceeds 1 and goes
    negative. There is no ``k`` to put a Wilson interval around. The branch
    therefore keeps ``|fa-fb| / max(|fa|, |fb|)`` — and the result says which
    objective produced it, because the two numbers are not on one scale.
    """
    rng = np.random.default_rng(5)
    a = _df(list(rng.normal(1500, 200, 400)))
    b = _df(list(rng.normal(400, 120, 400)))
    ctrl = rng.normal(700, 150, 400)
    base = dict(channel_key="gfp", min_threshold=0, max_threshold=3000, steps=512)

    raw = _optimize_thresh_frac_on_between_wells(a, b, **base)
    folded = _optimize_thresh_frac_on_between_wells(a, b, **base, ctrl_values=ctrl)

    assert raw.objective == "supported-separation"
    assert folded.objective == "relative-gap"
    # The count fields belong to the bounded objective and must not be invented
    # for the other one: a caller printing them would be printing a number that
    # does not describe the fractions the scan actually compared.
    assert (folded.k_a, folded.k_b, folded.youden) == (None, None, None)
    assert raw.k_a is not None


def test_the_fold_change_branch_will_not_score_a_cut_nothing_supports():
    """The coarse stand-in for the bound, on the branch that cannot have one.

    Well A holds ten bright cells in a thousand; Well B holds none that bright.
    The relative gap is at its maximum out there and has no N term to stop it,
    which is the reported defect. ``floor_unsupported`` blanks every candidate
    that leaves neither well ten cells, so the scan cannot land on it.
    """
    values_a = [200.0] * 990 + [5000.0] * 10
    values_b = [200.0] * 800 + [900.0] * 200
    a, b = _df(values_a), _df(values_b)
    ctrl = np.array([200.0] * 900 + [900.0] * 100)
    base = dict(channel_key="gfp", min_threshold=0, max_threshold=6000,
                steps=2001, ctrl_values=ctrl, op_ctrl="subtract")

    result = _optimize_thresh_frac_on_between_wells(a, b, **base)

    assert result.support_floor == 10
    # The ten-cell tail is out of reach; the cut lands where a population still
    # stands behind it.
    assert result.threshold < 5000.0
    assert max((np.asarray(values_a) > result.threshold).sum(),
               (np.asarray(values_b) > result.threshold).sum()) >= 10


def test_the_floor_shrinks_to_what_a_small_pair_of_wells_can_spare():
    """Ten cells is the ask, not the rule.

    A floor of ten against two wells of a dozen cells would reject every cut
    worth making, so it drops to a tenth of the larger well — never below one,
    because a cut with nothing above it in either well is not a cut.
    """
    thresholds = np.linspace(0.0, 100.0, 51)
    scores = np.linspace(0.0, 1.0, 51)
    tiny_a = np.array([10.0, 90.0, 95.0])
    tiny_b = np.array([5.0, 8.0, 9.0])

    out, floor = floor_unsupported(scores, tiny_a, tiny_b, thresholds)

    assert floor == 1
    # Above 95 nothing is left in either well, so those candidates are blanked
    # and the ones below are untouched.
    assert np.isneginf(out[thresholds > 95.0]).all()
    assert np.array_equal(out[thresholds < 95.0], scores[thresholds < 95.0])


def test_the_floor_stands_down_when_it_would_exclude_every_candidate():
    """``support_floor == 0`` on the result is how the caller can see it happened.

    The Min/Max fields are a user's to type, and a range sitting entirely above
    the data leaves no candidate with a cell above it. Masking all of them would
    turn a bad range into "these wells do not separate", which is a different
    and wrong answer. The scan reports what it can and says the floor was not
    applied.
    """
    values = np.array([1.0, 2.0, 3.0])
    thresholds = np.linspace(500.0, 900.0, 21)
    scores = np.linspace(0.0, 1.0, 21)

    out, floor = floor_unsupported(scores, values, values, thresholds)

    assert floor == 0
    assert np.array_equal(out, scores)
