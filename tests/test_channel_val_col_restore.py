"""Regression: the active value column must be re-derived after a view-state
restore that sets the channel and metric in separate steps.

view_state restore calls set_active_channel (which derives active_val_col from
the metric as it stood *before* the restore), then field-sets the restored
metric (combo-sync only). Without a final sync_val_col() the value column stays
``<channel>_<old_metric>`` and the first post-load plot shows the wrong data
until the user toggles the channel. This pins the helper that fixes it.
"""

from __future__ import annotations

from well_viewer.core.channel_state import ChannelState


def test_sync_val_col_re_derives_after_restore_order():
    cs = ChannelState()

    # 1) set_active_channel-style derive with the PRE-restore metric.
    cs.active_channel = "rfp"
    cs.active_metric = "mean_intensity"
    cs.sync_val_col()
    assert cs.active_val_col == "rfp_mean_intensity"

    # 2) metric restored by direct field assignment (no re-derive) → stale.
    cs.active_metric = "smfish_count"
    assert cs.active_val_col == "rfp_mean_intensity"   # the bug

    # 3) the fix: sync_val_col() re-derives from channel + restored metric.
    cs.sync_val_col()
    assert cs.active_val_col == "rfp_smfish_count"


def test_sync_val_col_leaves_ratio_and_shape_untouched():
    # A ratio key is its own value column; sync_val_col must not clobber it.
    cs = ChannelState()
    cs.active_val_col = "ratio:gfp_over_rfp"
    cs.sync_val_col()
    assert cs.active_val_col == "ratio:gfp_over_rfp"

    cs2 = ChannelState()
    cs2.active_val_col = "shape_area"
    cs2.sync_val_col()
    assert cs2.active_val_col == "shape_area"
