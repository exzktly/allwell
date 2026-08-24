"""Hiding a group keeps the plate's statistics; it does not recompute them.

Showing and hiding a replicate set used to run through
``invalidate_stats_cache``, which discards every cached statistic on the plate.
On a twelve-group plate that meant paying for twelve groups' worth of
aggregation each time one group was toggled off — and the eleven untouched
groups' results were thrown away with it.

Nothing about a visibility toggle can change a computed value.
``active_replicate_sets`` filters hidden sets out *before* anything is computed,
and no cache key carries ``hidden``: the keys are the set's name, its wells, the
timepoint, the threshold, and the gate and ratio signatures. That was verified by
recomputation rather than by reading — a full collect over twelve groups of eight
wells fills 312 entries, and toggling one group then recomputing all of them
gives 312 comparable keys with zero value differences.
"""

from __future__ import annotations

from well_viewer.core.selection_controller import (
    invalidate_stats_cache,
    note_group_visibility_changed,
)


class _Slice:
    override_version = 0


class _State:
    def __init__(self):
        from well_viewer.core.stats_cache_state import StatsCacheState
        self.stats_cache = StatsCacheState()

    def feature_slice(self, name):
        return None


class _Runtime:
    class image_table:
        inclusion_changed_cb = None


class _Ctx:
    def __init__(self):
        self.state = _State()
        self.runtime = _Runtime()


def _fill(ctx, n=5):
    for i in range(n):
        ctx.state.stats_cache.cache[("rep", f"G{i}", (), 0.0)] = (float(i), 0.0)
    return dict(ctx.state.stats_cache.cache)


def test_a_visibility_change_keeps_every_cached_statistic(monkeypatch):
    """The behaviour this change exists to produce."""
    monkeypatch.setattr(
        "well_viewer.core.plot_toggles.refresh_fov_btn_state", lambda ctx: None)
    ctx = _Ctx()
    before = _fill(ctx)
    note_group_visibility_changed(ctx)
    assert ctx.state.stats_cache.cache == before, (
        "toggling a group's visibility discarded cached statistics that a "
        "visibility change cannot have invalidated"
    )


def test_a_definition_change_still_discards_them(monkeypatch):
    """The other half: narrowing the visibility path must not narrow this one.

    Editing which wells a group contains, or changing a gate, genuinely does
    invalidate every cached statistic, and ``invalidate_stats_cache`` must keep
    doing so.
    """
    monkeypatch.setattr(
        "well_viewer.core.plot_toggles.refresh_fov_btn_state", lambda ctx: None)
    ctx = _Ctx()
    _fill(ctx)
    invalidate_stats_cache(ctx)
    assert ctx.state.stats_cache.cache == {}, (
        "a group-definition change left stale statistics behind"
    )


def test_the_visibility_path_still_syncs_the_fov_button(monkeypatch):
    """It is the one thing a toggle does still need from the old call."""
    calls = []
    monkeypatch.setattr(
        "well_viewer.core.plot_toggles.refresh_fov_btn_state",
        lambda ctx: calls.append(ctx))
    ctx = _Ctx()
    note_group_visibility_changed(ctx)
    assert calls == [ctx], "the per-FOV button bank was left unsynced"
