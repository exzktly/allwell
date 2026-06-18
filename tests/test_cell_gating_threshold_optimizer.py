from __future__ import annotations

import pandas as pd
import pytest

from well_viewer.domain.ratio_models import RatioMetric
from well_viewer.features.grouping.cell_gating_tab_view import (
    _optimize_thresh_frac_on_between_wells,
)


def _df(values, *, included=None, den=None):
    if included is None:
        included = [1] * len(values)
    data = {
        "area_px": [100] * len(values),
        "Included": included,
        "gfp_mean_intensity": values,
    }
    if den is not None:
        data["mcherry_mean_intensity"] = den
    return pd.DataFrame(data)


def test_optimizer_maximizes_absolute_fraction_difference():
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
    assert result.difference == pytest.approx(0.75)
    assert result.n_a == 4
    assert result.n_b == 4


def test_optimizer_respects_included_mask():
    a = _df([0, 100], included=[1, 0])
    b = _df([0, 100], included=[1, 1])

    result = _optimize_thresh_frac_on_between_wells(
        a,
        b,
        channel_key="gfp",
        min_threshold=50,
        max_threshold=50,
        steps=2,
    )

    assert result.frac_a == pytest.approx(0.0)
    assert result.frac_b == pytest.approx(0.5)
    assert result.difference == pytest.approx(0.5)
    assert result.n_a == 1
    assert result.n_b == 2


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


def test_optimizer_rejects_invalid_range_and_empty_values():
    with pytest.raises(ValueError, match="max threshold"):
        _optimize_thresh_frac_on_between_wells(
            _df([1]), _df([2]), channel_key="gfp",
            min_threshold=10, max_threshold=1, steps=2,
        )

    with pytest.raises(ValueError, match="Well A has no included finite values"):
        _optimize_thresh_frac_on_between_wells(
            _df([1], included=[0]), _df([2]), channel_key="gfp",
            min_threshold=0, max_threshold=1, steps=2,
        )
