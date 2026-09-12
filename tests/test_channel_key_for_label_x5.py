"""Audit X5 — ``channel_key_for_label`` must not lossily lowercase a ratio
label into a bogus real-channel token when ``_label_to_channel_key`` hasn't
been built yet.

A ratio read in that window (dropdown populated before the map) previously
resolved ``"GFP/MCHERRY"`` → ``"gfp/mcherry"`` (a nonexistent real channel)
instead of the ``ratio:<name>`` key. The fallback now consults the ratio
metrics directly.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core.channel_state import ChannelState
from review.core.channel_state_controller import channel_key_for_label
from review.domain.ratio_models import RatioMetric


def _ratio(name="ng", num="gfp", den="mcherry"):
    return RatioMetric(
        name=name,
        numerator_channel=num, numerator_metric="mean_intensity",
        denominator_channel=den, denominator_metric="mean_intensity",
    )


def _app(ratios, *, with_map):
    channel = ChannelState(fluor_channels=["gfp", "mcherry"], ratio_metrics=list(ratios))
    app = SimpleNamespace(_app_state=SimpleNamespace(channel=channel))
    # channel_key_for_label resolves ratio labels via ratio_label_for(ctx) (A8).
    app._ctx = SimpleNamespace(state=app._app_state, app=app)
    if with_map:
        # Simulate the built map (real channels + ratio labels).
        app._label_to_channel_key = {"GFP": "gfp", "MCHERRY": "mcherry"}
        for r in ratios:
            app._label_to_channel_key[r.display_label()] = r.key()
    return app


def test_ratio_label_resolves_via_map_when_built():
    r = _ratio()
    app = _app([r], with_map=True)
    assert channel_key_for_label(app._ctx, "GFP/MCHERRY") == r.key()


def test_ratio_label_resolves_before_map_built():
    # X5: no _label_to_channel_key yet → must still return ratio:<name>, NOT
    # the lowercased "gfp/mcherry".
    r = _ratio()
    app = _app([r], with_map=False)
    assert not hasattr(app, "_label_to_channel_key")
    assert channel_key_for_label(app._ctx, "GFP/MCHERRY") == r.key()
    assert channel_key_for_label(app._ctx, "GFP/MCHERRY") != "gfp/mcherry"


def test_disambiguated_ratio_label_resolves_before_map():
    # Two ratios share the GFP/MCHERRY base label → labels carry "[name]".
    r1, r2 = _ratio("a"), _ratio("b")
    app = _app([r1, r2], with_map=False)
    assert channel_key_for_label(app._ctx, "GFP/MCHERRY [a]") == r1.key()
    assert channel_key_for_label(app._ctx, "GFP/MCHERRY [b]") == r2.key()


def test_real_channel_still_lowercases():
    app = _app([_ratio()], with_map=False)
    assert channel_key_for_label(app._ctx, "GFP") == "gfp"
