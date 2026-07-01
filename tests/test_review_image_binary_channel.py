"""Segmentation 'Binary (≥ ON threshold)' must gate on the channel being VIEWED.

Regression guard: the binary mask keyed off the global plotting channel
(active_val_col / active-channel threshold), so it binarised the wrong channel
when the displayed image channel differed. It must key off active_image_channel.
"""
from __future__ import annotations

from types import SimpleNamespace

from well_viewer.features.image_browser.review_image.controller import _binary_gate_columns


def _app(*, image_channel, metric="mean_intensity", active_val_col="mcherry_mean_intensity",
         thresholds=None):
    thresholds = thresholds or {}
    return SimpleNamespace(
        _app_state=SimpleNamespace(channel=SimpleNamespace(
            active_image_channel=image_channel,
            active_metric=metric,
            active_val_col=active_val_col,
            ratio_index={},
        )),
        _get_thresh_frac_on=lambda ch=None: thresholds.get(ch, -1.0),
    )


def test_gate_uses_displayed_image_channel_not_plotting_channel():
    # Viewing GFP while the plots are on mCherry → gate on GFP, not mCherry.
    app = _app(image_channel="gfp", active_val_col="mcherry_mean_intensity",
               thresholds={"gfp": 60.0, "mcherry": 10.0})
    val_col, threshold = _binary_gate_columns(app)
    assert val_col == "gfp_mean_intensity"     # displayed channel's column
    assert threshold == 60.0                   # displayed channel's ON threshold


def test_gate_respects_active_metric():
    app = _app(image_channel="dapi", metric="median", thresholds={"dapi": 5.0})
    val_col, _ = _binary_gate_columns(app)
    assert val_col == "dapi_median"


def test_gate_falls_back_to_plotting_column_when_no_image_channel():
    app = _app(image_channel="", active_val_col="gfp_mean_intensity",
               thresholds={None: 42.0})
    val_col, threshold = _binary_gate_columns(app)
    assert val_col == "gfp_mean_intensity"
    assert threshold == 42.0
