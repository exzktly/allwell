"""A ratio's epsilon is the floor its denominator is clamped to — on every surface that computes one.

``RatioMetric`` states the rule and ``data_loading.resolve_value_series``
applies it for every plot, gate and export: ``num / max(den, epsilon)``.
The Cell Gating ratio CDF — the curve a ratio's ON cutoff is read from — and
the Image Table's ratio image each computed ``num / (den + epsilon)`` instead.
For a numerator of 100 and denominators 50, 0.5, −0.5 and −3 with epsilon 1,
the plots hold 2, 100, 100, 100; the CDF drew 1.96, 66.7, 200 and dropped the
last cell (a negative ratio).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data.data_loading import resolve_value_series
from review.domain.ratio_models import RatioMetric

RATIO = RatioMetric(name="r", numerator_channel="gfp", numerator_metric="mean_intensity",
                    denominator_channel="mcherry", denominator_metric="mean_intensity", epsilon=1.0)
DENOMINATORS = [50.0, 0.5, -0.5, -3.0]
CLAMPED = [2.0, 100.0, 100.0, 100.0]            # 100 / max(den, 1), by hand


def _cells() -> pd.DataFrame:
    return pd.DataFrame({"gfp_mean_intensity": 100.0, "mcherry_mean_intensity": DENOMINATORS,
                         "area_px": 100.0, "fov": "1", "timepoint_hours": 0.0})


def test_the_plots_clamp_the_denominator():
    values = resolve_value_series(_cells(), RATIO.key(), {RATIO.key(): RATIO})
    assert values.tolist() == CLAMPED


def test_the_cell_gating_cdf_holds_the_values_the_plots_hold():
    from review.features.home.cell_gating.controller import _first_frame_values

    _areas, _fluor, ratios = _first_frame_values(_cells(), ["gfp", "mcherry"], [RATIO])
    assert ratios[RATIO.display_label()].tolist() == CLAMPED


def test_the_cdf_still_divides_plainly_with_no_epsilon_and_drops_a_zero_denominator():
    from review.features.home.cell_gating.controller import _first_frame_values

    plain = RatioMetric(name="r", numerator_channel="gfp", numerator_metric="mean_intensity",
                        denominator_channel="mcherry", denominator_metric="mean_intensity")
    df = _cells()
    df["mcherry_mean_intensity"] = [50.0, 0.5, 0.0, 4.0]
    _areas, _fluor, ratios = _first_frame_values(df, ["gfp", "mcherry"], [plain])
    assert ratios[plain.display_label()].tolist() == [2.0, 200.0, 25.0]


def test_the_ratio_image_clamps_its_denominator_too(monkeypatch):
    from review.features.image_browser.image_table import image_compose

    planes = {"GFP": np.full((2, 2), 100.0, dtype=np.float32),
              "MCHERRY": np.array([[50.0, 0.5], [0.0, 4.0]], dtype=np.float32)}
    monkeypatch.setattr(image_compose, "_load_array",
                        lambda _ctx, _cache, _well, channel, _tp, _fov, *, use_tophat: planes[channel])
    image = image_compose._load_ratio_array(None, None, "A1", RATIO, "0", "1", use_tophat=False)
    assert image.ravel().tolist() == pytest.approx([2.0, 100.0, 100.0, 25.0])
