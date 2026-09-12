"""Segmentation 'Binary (≥ ON threshold)' must gate on the channel being VIEWED.

Regression guard: the binary mask keyed off the global plotting channel
(active_val_col / active-channel threshold), so it binarised the wrong channel
when the displayed image channel differed. It must key off active_image_channel.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.features.image_browser.review_image.controller import _binary_gate_columns


def _ctx(*, image_channel, metric="mean_intensity", active_val_col="mcherry_mean_intensity",
         active_channel="gfp", thresh_frac_on=None, threshold=-1.0):
    # A8 D4: _binary_gate_columns is ctx-native — it resolves the per-channel ON
    # threshold through the ctx get_thresh_frac_on leaf, which reads the typed
    # CellGatingState slice (falling back to channel.threshold when unset).
    return SimpleNamespace(
        state=SimpleNamespace(
            channel=SimpleNamespace(
                active_image_channel=image_channel,
                active_metric=metric,
                active_val_col=active_val_col,
                active_channel=active_channel,
                threshold=threshold,
            ),
            cell_gating=SimpleNamespace(thresh_frac_on=thresh_frac_on or {}),
        ),
    )


def test_gate_uses_displayed_image_channel_not_plotting_channel():
    # Viewing GFP while the plots are on mCherry → gate on GFP, not mCherry.
    ctx = _ctx(image_channel="gfp", active_val_col="mcherry_mean_intensity",
               thresh_frac_on={"gfp": 60.0, "mcherry": 10.0})
    val_col, threshold = _binary_gate_columns(ctx)
    assert val_col == "gfp_mean_intensity"     # displayed channel's column
    assert threshold == 60.0                   # displayed channel's ON threshold


def test_gate_respects_active_metric():
    ctx = _ctx(image_channel="dapi", metric="median", thresh_frac_on={"dapi": 5.0})
    val_col, _ = _binary_gate_columns(ctx)
    assert val_col == "dapi_median"


def test_gate_falls_back_to_plotting_column_when_no_image_channel():
    ctx = _ctx(image_channel="", active_val_col="gfp_mean_intensity",
               active_channel="gfp", thresh_frac_on={"gfp": 42.0})
    val_col, threshold = _binary_gate_columns(ctx)
    assert val_col == "gfp_mean_intensity"
    assert threshold == 42.0
