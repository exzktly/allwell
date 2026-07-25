from __future__ import annotations

import pandas as pd
import pytest

from well_viewer.domain.ratio_models import RatioMetric
from well_viewer.features.grouping.cell_gating.gating_compute import _optimize_thresh_frac_on_between_wells


def _df(values, *, included=None, den=None, qc=None, crop=None):
    if included is None:
        included = [1] * len(values)
    data = {
        "area_px": [100] * len(values),
        "Included": included,
        "gfp_mean_intensity": values,
    }
    if den is not None:
        data["mcherry_mean_intensity"] = den
    if qc is not None:
        data["QC_Pass"] = qc
    if crop is not None:
        data["Crop_Pass"] = crop
    return pd.DataFrame(data)


def test_optimizer_maximizes_relative_fraction_gap():
    # Optimizer maximises ``|fa - fb| / max(fa, fb)`` (relative gap), so
    # B is fully off and A is partially on ⇒ the gap is 100% of the larger
    # fraction at every threshold ≤ A's max, even when the absolute
    # difference is shrinking. argmax-on-tie returns the first index — the
    # range Min — same threshold the absolute metric used to pick here.
    a = _df([0, 10, 20, 30])
    b = _df([0, 0, 0, 0])

    result = _optimize_thresh_frac_on_between_wells(
        a,
        b,
        channel_key="gfp",
        min_threshold=0,
        max_threshold=30,
        steps=4,
    )

    assert result.threshold == pytest.approx(0.0)
    assert result.frac_a == pytest.approx(0.75)
    assert result.frac_b == pytest.approx(0.0)
    assert result.difference == pytest.approx(1.0)
    assert result.n_a == 4
    assert result.n_b == 4


def test_optimizer_normalisation_prefers_relative_over_absolute():
    # Both wells have the same number of bright cells but A also has a long
    # background tail. At a high threshold, A is 1% on and B is 0% on — an
    # absolute gap of 0.01, but a 100% relative gap. At a low threshold,
    # both wells are nearly fully on — a 0.05 absolute gap but only a few-%
    # relative gap. Absolute argmax would land at the low threshold; the
    # relative metric correctly picks the high one (where the populations
    # actually separate).
    a_values = [0] * 95 + [500] * 5            # 5% bright tail above background
    b_values = [0] * 90 + [50] * 10            # 10% mid-bright, no bright tail
    a = _df(a_values)
    b = _df(b_values)

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp",
        min_threshold=0, max_threshold=500, steps=501,
    )

    # At threshold >= 50 (no B cells above 50), fa = 0.05, fb = 0 ⇒ relative
    # gap = 1.0. The metric must pick a threshold in that high band, not
    # the noise-floor band where both wells are nearly fully off and the
    # absolute gap is small but the relative gap is also small.
    assert result.threshold >= 50.0
    assert result.frac_a == pytest.approx(0.05)
    assert result.frac_b == pytest.approx(0.0)
    assert result.difference == pytest.approx(1.0)


def test_optimizer_ignores_included_gate():
    # ``Included`` is set by the asynchronous gating worker from FluorGating —
    # the very threshold the optimizer is helping pick. Filtering by it would
    # be circular (and would silently shift the answer the first time the
    # worker writes the column). The optimizer must see every QC+Crop-valid
    # cell regardless of the per-channel fluorescence gate.
    a = _df([0, 100], included=[1, 0])  # the "on" cell is gated out by FluorGating
    b = _df([0, 100], included=[1, 1])

    result = _optimize_thresh_frac_on_between_wells(
        a,
        b,
        channel_key="gfp",
        min_threshold=50,
        max_threshold=50,
        steps=2,
    )

    # Both wells contribute both cells: A is 50% on, B is 50% on ⇒ no difference.
    assert result.frac_a == pytest.approx(0.5)
    assert result.frac_b == pytest.approx(0.5)
    assert result.difference == pytest.approx(0.0)
    assert result.n_a == 2
    assert result.n_b == 2


def test_optimizer_excludes_qc_and_crop_failures():
    # QC_Pass / Crop_Pass are independent of the fluorescence gate and ARE
    # honoured — they remove debris and out-of-region cells from the population
    # the optimizer scans.
    a = _df([0, 100], qc=[1, 0])         # the "on" cell fails QC
    b = _df([0, 100], crop=[0, 1])       # the "off" cell falls outside the crop

    result = _optimize_thresh_frac_on_between_wells(
        a,
        b,
        channel_key="gfp",
        min_threshold=50,
        max_threshold=50,
        steps=2,
    )

    assert result.frac_a == pytest.approx(0.0)
    assert result.frac_b == pytest.approx(1.0)
    assert result.n_a == 1
    assert result.n_b == 1


def test_optimizer_stable_across_gating():
    # Re-running the optimizer after the background gating worker has written
    # FluorGating's choices into ``Included`` must return the same threshold —
    # otherwise the first Optimize is right and every later one drifts (the
    # original bug: argmax landed at the range edge once Included was filled in).
    values_a = [0, 5, 10, 50, 100, 200]
    values_b = [0, 0, 0, 0, 5, 10]

    a_pre = _df(values_a)   # cached frame: Included all 1 (no gating pass yet)
    b_pre = _df(values_b)

    # Simulate FluorGating's pass: Included becomes ``intensity > FluorGating``
    # for some chosen FluorGating (here 8). This is the asynchronous mutation
    # the optimizer used to silently filter itself by.
    fluor_gate = 8.0
    a_post = _df(values_a, included=[int(v > fluor_gate) for v in values_a])
    b_post = _df(values_b, included=[int(v > fluor_gate) for v in values_b])

    pre = _optimize_thresh_frac_on_between_wells(
        a_pre, b_pre, channel_key="gfp",
        min_threshold=0, max_threshold=200, steps=201,
    )
    post = _optimize_thresh_frac_on_between_wells(
        a_post, b_post, channel_key="gfp",
        min_threshold=0, max_threshold=200, steps=201,
    )

    assert post.threshold == pytest.approx(pre.threshold)
    assert post.difference == pytest.approx(pre.difference)
    assert post.n_a == pre.n_a
    assert post.n_b == pre.n_b


def test_optimizer_supports_ratio_channels():
    ratio = RatioMetric(
        name="gfp_over_mcherry",
        numerator_channel="gfp",
        numerator_metric="mean_intensity",
        denominator_channel="mcherry",
        denominator_metric="mean_intensity",
    )
    a = _df([10, 20], den=[10, 10])
    b = _df([40, 80], den=[10, 10])

    result = _optimize_thresh_frac_on_between_wells(
        a,
        b,
        channel_key=ratio.key(),
        min_threshold=2,
        max_threshold=5,
        steps=4,
        ratios={ratio.key(): ratio},
    )

    assert result.threshold == pytest.approx(2.0)
    assert result.frac_a == pytest.approx(0.0)
    assert result.frac_b == pytest.approx(1.0)
    assert result.difference == pytest.approx(1.0)


def test_optimizer_returns_first_threshold_at_max_difference():
    # Two well-separated populations: A all 100, B all 1000. Every threshold in
    # [100, 1000) yields the maximal |Δ| (=1.0). The optimizer reports the
    # threshold where the difference is biggest; np.argmax returns the FIRST
    # such threshold, i.e. the lowest one attaining the max (100).
    a = _df([100] * 50)
    b = _df([1000] * 50)

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp",
        min_threshold=0, max_threshold=1100, steps=1101,
    )

    assert result.difference == pytest.approx(1.0)
    assert result.threshold == pytest.approx(100.0)


def test_optimizer_filters_by_timepoint():
    a = pd.DataFrame({
        "area_px": [100] * 4,
        "Included": [1] * 4,
        "timepoint_hours": [0.0, 0.0, 24.0, 24.0],
        "gfp_mean_intensity": [0, 0, 100, 100],
    })
    b = pd.DataFrame({
        "area_px": [100] * 4,
        "Included": [1] * 4,
        "timepoint_hours": [0.0, 0.0, 24.0, 24.0],
        "gfp_mean_intensity": [0, 0, 0, 0],
    })

    # At 24 h, A is all-on above 50 and B is all-off ⇒ |Δ| = 1.0 over 2 cells each.
    at_24 = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp",
        min_threshold=50, max_threshold=50, steps=2,
        timepoint=("hours", 24.0),
    )
    assert at_24.frac_a == pytest.approx(1.0)
    assert at_24.frac_b == pytest.approx(0.0)
    assert at_24.difference == pytest.approx(1.0)
    assert at_24.n_a == 2 and at_24.n_b == 2

    # At 0 h both wells are off ⇒ no difference, and only the 2 tp=0 rows count.
    at_0 = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp",
        min_threshold=50, max_threshold=50, steps=2,
        timepoint=("hours", 0.0),
    )
    assert at_0.difference == pytest.approx(0.0)
    assert at_0.n_a == 2 and at_0.n_b == 2


def test_optimizer_rejects_invalid_range_and_empty_values():
    with pytest.raises(ValueError, match="max threshold"):
        _optimize_thresh_frac_on_between_wells(
            _df([1]), _df([2]), channel_key="gfp",
            min_threshold=10, max_threshold=1, steps=2,
        )

    with pytest.raises(ValueError, match="Well A has no valid finite values"):
        # QC_Pass=0 removes the only row, leaving no valid finite values.
        _optimize_thresh_frac_on_between_wells(
            _df([1], qc=[0]), _df([2]), channel_key="gfp",
            min_threshold=0, max_threshold=1, steps=2,
        )
