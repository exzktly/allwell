"""The threshold range must never be the previous dataset's.

``recalculate_threshold`` rebuilds the (min, max) span the intensity sliders
cover from every loaded well. When the active value column has no values at all
— a dataset with no wells yet, or one whose CSVs do not carry the column the
previous plate was showing — it returned early, before ``apply_range``.
``load_controller.begin_load`` clears the per-column *cache* and nothing else,
so ``ChannelState.threshold_min`` / ``threshold_max`` kept the numbers of the
plate that had just been closed: the sliders spanned a range belonging to
another dataset, and every threshold taken off them was quietly wrong.

Undefined is not unchanged. The range goes back to ``ChannelState``'s declared
defaults, which is a state the app can draw and a state the user can see is not
a measurement.
"""

from __future__ import annotations

from types import SimpleNamespace

from review.core import channel_state_controller
from review.core.channel_state import ChannelState
from review.core.widget_registry import WidgetRegistry


def _ctx(channel_state: ChannelState, well_paths: dict | None = None):
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                well_paths=dict(well_paths or {}), pipeline_info={},
            ),
            channel=channel_state,
            feature_slice=lambda name: None,
        ),
        widgets=WidgetRegistry(SimpleNamespace()),
        features=None,
        runtime=SimpleNamespace(
            cell_gating=SimpleNamespace(fluor_gate_edits=None),
        ),
    )


def test_a_dataset_with_no_values_does_not_inherit_the_previous_range():
    channel_state = ChannelState()
    # The plate just closed was bright: the sliders spanned 120 – 48000.
    channel_state.apply_range(120.0, 48000.0)
    channel_state.cache_range(channel_state.active_val_col, 120.0, 48000.0)
    # begin_load drops the cache — the one thing it does clear.
    channel_state.threshold_range_cache.clear()

    channel_state_controller.recalculate_threshold(_ctx(channel_state))

    fresh = ChannelState()
    assert (channel_state.threshold_min, channel_state.threshold_max) == (
        fresh.threshold_min, fresh.threshold_max
    ), (
        "the sliders kept the previous dataset's intensity range: "
        f"{channel_state.threshold_min} – {channel_state.threshold_max}"
    )


def test_nothing_is_cached_for_a_column_with_no_values():
    """A default range must not be remembered as this column's measured one —
    it would then be reused when the user switches back to the channel."""
    channel_state = ChannelState()
    channel_state.apply_range(120.0, 48000.0)

    channel_state_controller.recalculate_threshold(_ctx(channel_state))

    assert channel_state.cached_range(channel_state.active_val_col) is None


def test_reset_threshold_range_uses_the_declared_defaults():
    """Read off a fresh slice rather than literals, so the two cannot drift."""
    channel_state = ChannelState()
    channel_state.apply_range(7.0, 9.0)

    channel_state_controller.reset_threshold_range(channel_state)

    fresh = ChannelState()
    assert channel_state.threshold_min == fresh.threshold_min
    assert channel_state.threshold_max == fresh.threshold_max
