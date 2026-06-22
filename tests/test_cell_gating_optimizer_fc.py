"""Fold-change adjustment of the Cell Gating ThreshFracOn optimizer scan."""
from __future__ import annotations

import numpy as np
import pandas as pd

from well_viewer.features.grouping.cell_gating_tab_view import (
    _optimize_thresh_frac_on_between_wells,
    _scan_fc_fractions,
)


def _df(values):
    return pd.DataFrame({"area_px": [100] * len(values),
                         "Included": [1] * len(values),
                         "gfp_mean_intensity": values})


def test_scan_fc_off_matches_raw_fraction_gap():
    th = np.linspace(0, 10, 11)
    a = np.array([0, 1, 2, 8, 9, 10], float)
    b = np.array([0, 0, 0, 0, 0, 1], float)
    fa, fb, diffs = _scan_fc_fractions(th, a, b)
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
    _fa_raw, _fb_raw, d_raw = _scan_fc_fractions(th, a, b)
    _fa, _fb, diffs = _scan_fc_fractions(th, a, b, ctrl_values=ctrl, op_ctrl="ratio")
    assert np.all(np.isfinite(diffs))
    assert np.allclose(d_raw, diffs)


def test_scan_fc_vs_baseline_reshapes_the_metric():
    # vs-t0 uses a DIFFERENT denominator per well, so it genuinely changes the
    # relative-gap curve (unlike the shared-control ratio above).
    th = np.linspace(0, 10, 11)
    a = np.array([5, 6, 7, 8, 9, 10], float)
    b = np.array([0, 1, 2, 3, 4, 5], float)
    base_a = np.array([8, 9, 10], float)
    base_b = np.array([0, 0, 1], float)
    _fa_raw, _fb_raw, d_raw = _scan_fc_fractions(th, a, b)
    _fa, _fb, diffs = _scan_fc_fractions(
        th, a, b, base_values_a=base_a, base_values_b=base_b, op_t0="ratio")
    assert np.all(np.isfinite(diffs))
    assert not np.allclose(d_raw, diffs)


def test_scan_fc_subtract_control_matches_scale_bar_value():
    from well_viewer.domain.fold_change import scale_bar_value
    th = np.array([0.0, 5.0])
    a = np.array([10.0, 10.0], float)  # frac-on = 1.0 at t=0, 0.5 at t=5
    b = np.array([0.0, 0.0], float)
    ctrl = np.array([10.0, 0.0], float)  # frac = 1.0 at every t>... actually >0 → 0.5
    fa, fb, _ = _scan_fc_fractions(th, a, b, ctrl_values=ctrl, op_ctrl="subtract")
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
