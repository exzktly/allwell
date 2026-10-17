"""Pure-Python unit tests for ``ChannelState``.

No Qt, no app — these exercise the invariant-enforcing methods directly. The
point of the teardown is that behaviour moved *into* the state type; this suite
is what verifies it independently of ``ReviewApp``.
"""

from __future__ import annotations

from review.domain.ratio_models import RatioMetric
from review.core.channel_state import ChannelState


def _ratio(name: str = "ng_ratio") -> RatioMetric:
    return RatioMetric(
        name=name,
        numerator_channel="gfp",
        numerator_metric="mean_intensity",
        denominator_channel="mcherry",
        denominator_metric="mean_intensity",
    )


# ── derived value column ─────────────────────────────────────────────────────

def test_defaults():
    cs = ChannelState()
    assert cs.active_channel == "gfp"
    assert cs.active_val_col == "gfp_mean_intensity"


def test_derive_val_col():
    cs = ChannelState(active_channel="mcherry", active_metric="total_intensity")
    assert cs.derive_val_col() == "mcherry_total_intensity"


def test_sync_val_col_real_channel():
    cs = ChannelState(active_channel="mcherry", active_val_col="stale")
    cs.sync_val_col()
    assert cs.active_val_col == "mcherry_mean_intensity"


def test_sync_val_col_is_noop_for_ratio():
    cs = ChannelState(active_channel="ng", active_val_col="ratio:ng")
    cs.sync_val_col()
    assert cs.active_val_col == "ratio:ng"


# ── metric normalisation ─────────────────────────────────────────────────────

def test_normalize_metric_drops_stale_smfish():
    cs = ChannelState(active_channel="gfp", active_metric="smfish_count")
    cs.normalize_metric()
    assert cs.active_metric == "mean_intensity"


def test_normalize_metric_keeps_smfish_for_smfish_channel():
    cs = ChannelState(
        active_channel="cy5", active_metric="smfish_count",
        smfish_channels={"cy5"},
    )
    cs.normalize_metric()
    assert cs.active_metric == "smfish_count"


# ── channel switching ────────────────────────────────────────────────────────

def test_set_active_channel_changes_state():
    cs = ChannelState()
    assert cs.set_active_channel("mcherry") is True
    assert cs.active_channel == "mcherry"
    assert cs.active_val_col == "mcherry_mean_intensity"


def test_set_active_channel_noop_when_same():
    cs = ChannelState(active_channel="gfp", active_val_col="gfp_mean_intensity")
    assert cs.set_active_channel("gfp") is False


def test_set_active_channel_resets_stale_smfish_metric():
    cs = ChannelState(active_channel="gfp", active_metric="smfish_count")
    cs.set_active_channel("mcherry")
    assert cs.active_metric == "mean_intensity"
    assert cs.active_val_col == "mcherry_mean_intensity"


def test_set_active_channel_keeps_metric_for_smfish_channel():
    cs = ChannelState(
        active_channel="gfp", active_metric="smfish_count",
        active_val_col="gfp_smfish_count", smfish_channels={"cy5"},
    )
    cs.set_active_channel("cy5")
    assert cs.active_metric == "smfish_count"
    assert cs.active_val_col == "cy5_smfish_count"


def test_set_active_channel_from_ratio_resets_metric():
    cs = ChannelState(
        active_channel="ng", active_metric="smfish_count",
        active_val_col="ratio:ng", smfish_channels={"cy5"},
    )
    assert cs.set_active_channel("cy5") is True
    assert cs.active_metric == "mean_intensity"
    assert cs.active_val_col == "cy5_mean_intensity"


# ── ratio channels ───────────────────────────────────────────────────────────

def test_set_active_ratio():
    cs = ChannelState()
    ratio = _ratio("ng")
    assert cs.set_active_ratio(ratio) is True
    assert cs.active_channel == "ng"
    assert cs.active_val_col == "ratio:ng"


def test_set_active_ratio_noop_when_same():
    cs = ChannelState(active_val_col="ratio:ng")
    assert cs.set_active_ratio(_ratio("ng")) is False


def test_resolve_ratio():
    cs = ChannelState(ratio_metrics=[_ratio("ng"), _ratio("other")])
    assert cs.resolve_ratio("ratio:ng").name == "ng"
    assert cs.resolve_ratio("ratio:missing") is None


def test_set_ratio_metrics_rebuilds_index():
    cs = ChannelState()
    cs.set_ratio_metrics([_ratio("ng"), _ratio("rr")])
    assert set(cs.ratio_index) == {"ratio:ng", "ratio:rr"}
    assert cs.ratio_index["ratio:ng"].name == "ng"


# ── threshold range ──────────────────────────────────────────────────────────

