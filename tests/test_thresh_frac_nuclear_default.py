"""The nuclear (segmentation) channel's ON/OFF threshold defaults to 0, not 50.

Regression guard. ThreshFracOn — "how bright must a cell be to count as ON" —
defaulted to 50.0 for every channel. That is the right starting point for a
fluorescence *readout* channel, but the nuclear channel is the one nuclei are
*found* with: every segmented cell is by construction a detection in it, so the
readout default only served to hide most nuclei from the Image Table's
threshold / binary view of that channel.

The dataset's nuclear channel is the ``nuclear_token`` recorded in
``pipeline_info.json``, which the viewer reads into
``ctx.state.channel.seg_channel_token``. Only that channel's default changes;
every other channel still starts at 50.0, and any value the user has actually
set continues to win over either default.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from well_viewer.core.channel_state_controller import get_thresh_frac_on
from well_viewer.domain.gating_state import build_gating_block, default_thresh_frac_on
from well_viewer.domain.viewer_state import read_pipeline_info

NUCLEAR = "rfp"
READOUT = "gfp"


def _ctx(*, saved=None, seg_token=NUCLEAR, active_channel=READOUT):
    """A minimal ctx carrying only what the threshold leaves read."""
    return SimpleNamespace(
        state=SimpleNamespace(
            channel=SimpleNamespace(
                active_channel=active_channel,
                active_image_channel="",
                active_metric="mean_intensity",
                active_val_col=f"{active_channel}_mean_intensity",
                smfish_channels=set(),
                fluor_channels=[READOUT, NUCLEAR],
                seg_channel_token=seg_token,
                threshold=50.0,
            ),
            cell_gating=SimpleNamespace(thresh_frac_on=dict(saved or {}), fluor_gates={}),
        ),
    )


# ── the default itself ───────────────────────────────────────────────────────

def test_nuclear_channel_defaults_to_zero():
    assert get_thresh_frac_on(_ctx(), NUCLEAR) == 0.0


def test_readout_channel_still_defaults_to_fifty():
    assert get_thresh_frac_on(_ctx(), READOUT) == 50.0


def test_no_nuclear_token_leaves_every_channel_at_fifty():
    # A dataset whose sidecar records no nuclear_token has no channel to
    # exempt — nothing silently drops to zero.
    ctx = _ctx(seg_token="")
    assert get_thresh_frac_on(ctx, NUCLEAR) == 50.0
    assert get_thresh_frac_on(ctx, READOUT) == 50.0


def test_nuclear_token_matching_is_case_insensitive():
    ctx = _ctx(seg_token="RFP")
    assert get_thresh_frac_on(ctx, "rfp") == 0.0
    assert get_thresh_frac_on(ctx, "RFP") == 0.0


def test_ratio_key_named_like_the_nuclear_channel_keeps_the_readout_default():
    assert default_thresh_frac_on(f"ratio:{NUCLEAR}", NUCLEAR) == 50.0


# ── a set value always wins over either default ──────────────────────────────

def test_saved_value_wins_on_the_nuclear_channel():
    ctx = _ctx(saved={NUCLEAR: 42.5})
    assert get_thresh_frac_on(ctx, NUCLEAR) == 42.5


def test_saved_value_wins_on_a_readout_channel():
    ctx = _ctx(saved={READOUT: 7.5, NUCLEAR: 3.0})
    assert get_thresh_frac_on(ctx, READOUT) == 7.5
    assert get_thresh_frac_on(ctx, NUCLEAR) == 3.0


def test_saved_readout_default_value_on_the_nuclear_channel_is_honoured():
    # 50.0 deliberately set on the nuclear channel is a value, not a default.
    ctx = _ctx(saved={NUCLEAR: 50.0})
    assert get_thresh_frac_on(ctx, NUCLEAR) == 50.0


# ── the channel is identified from pipeline_info.json::nuclear_token ─────────

def test_nuclear_channel_comes_from_pipeline_info(tmp_path: Path):
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema": "well_channel_fov",
        "fluor_tokens": [READOUT, NUCLEAR],
        "nuclear_token": NUCLEAR.upper(),
        "well_index": 0, "channel_index": 1, "fov_index": 2, "tp_index": 3,
    }))
    _parse, _tokens, _smfish, info = read_pipeline_info(tmp_path)
    assert info["nuclear_token"] == NUCLEAR

    ctx = _ctx(seg_token=info["nuclear_token"])
    assert get_thresh_frac_on(ctx, NUCLEAR) == 0.0
    assert get_thresh_frac_on(ctx, READOUT) == 50.0


# ── the Image Table's own render path ────────────────────────────────────────

def test_image_table_binary_view_of_the_nuclear_channel_uses_zero():
    from well_viewer.features.image_browser.review_image.collect import _binary_gate_columns
    ctx = _ctx()
    assert _binary_gate_columns(ctx, NUCLEAR) == (f"{NUCLEAR}_mean_intensity", 0.0)
    assert _binary_gate_columns(ctx, READOUT) == (f"{READOUT}_mean_intensity", 50.0)


# ── persistence diffs against the right default ──────────────────────────────

def test_default_values_stay_out_of_the_sidecar():
    block = build_gating_block(
        {}, {READOUT: 50.0, NUCLEAR: 0.0}, seg_channel_token=NUCLEAR)
    assert "thresh_frac_on" not in block


def test_a_nuclear_cutoff_set_to_the_readout_default_is_persisted():
    # Without the per-channel default this value diffs as "at default" and is
    # dropped, so it would reload as 0.0 — a set value silently discarded.
    block = build_gating_block({}, {NUCLEAR: 50.0}, seg_channel_token=NUCLEAR)
    assert block["thresh_frac_on"] == {NUCLEAR: 50.0}


def test_a_readout_cutoff_set_to_zero_is_still_persisted():
    block = build_gating_block({}, {READOUT: 0.0}, seg_channel_token=NUCLEAR)
    assert block["thresh_frac_on"] == {READOUT: 0.0}


# ── the Cell Gating tab seeds its rows with the same defaults ────────────────

def test_cell_gating_rows_seed_the_nuclear_channel_at_zero():
    # The tab's row build writes the default into the typed slice, so it has to
    # agree with the accessor — otherwise building the tab re-introduces 50.0
    # for the nuclear channel and the fix above is undone on first activation.
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    _qapp = QApplication.instance() or QApplication([])
    inner = QWidget()
    QVBoxLayout(inner)

    from well_viewer.features.grouping.cell_gating.controller import (
        _cell_gating_build_channel_controls,
    )
    ctx = _ctx()
    ctx.state.channel.ratio_metrics = []
    ctx.runtime = SimpleNamespace(cell_gating=SimpleNamespace(
        inner=inner, fluor_gate_edits={}, thresh_frac_edits={},
    ))
    _cell_gating_build_channel_controls(ctx)

    assert ctx.state.cell_gating.thresh_frac_on == {READOUT: 50.0, NUCLEAR: 0.0}
    assert ctx.runtime.cell_gating.thresh_frac_edits[NUCLEAR].text() == "0.0"
    assert ctx.runtime.cell_gating.thresh_frac_edits[READOUT].text() == "50.0"
