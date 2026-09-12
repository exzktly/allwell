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

from review.core.selection_controller import (
    _set_groups_hidden,
    invalidate_stats_cache,
    note_group_visibility_changed,
)


class _Slice:
    override_version = 0


class _State:
    def __init__(self):
        from review.core.plot_state import PlotState
        from review.core.stats_cache_state import StatsCacheState
        self.stats_cache = StatsCacheState()
        # The real ``PlotState``, not a namespace with the two fields this file
        # reads: ``rebuild_all`` hands the repaint to the scope coalescer, whose
        # dirty set lives here, and a stub missing it makes the fan-out
        # untestable from this file rather than making it not happen.
        self.plot = PlotState()

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
        "review.core.plot_toggles.refresh_fov_btn_state", lambda ctx: None)
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
        "review.core.plot_toggles.refresh_fov_btn_state", lambda ctx: None)
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
        "review.core.plot_toggles.refresh_fov_btn_state",
        lambda ctx: calls.append(ctx))
    ctx = _Ctx()
    note_group_visibility_changed(ctx)
    assert calls == [ctx], "the per-FOV button bank was left unsynced"


# ── Driven through the real toggle, not the helper ───────────────────────────
#
# The first version of this file asserted only that ``note_group_visibility_
# changed`` preserves the cache, and that was true while the app threw it away
# anyway: ``_set_groups_hidden`` called the notifier and then ``_rebuild_all()``
# beside it, whose first act is ``_invalidate_stats_cache()``. The step bought
# nothing and the suite was green. Asserting that a function *can* keep the
# cache is not asserting that toggling a group keeps it.


class _Window:
    """The seams ``_set_groups_hidden`` -> ``rebuild_all`` actually touches."""

    def __init__(self, ctx):
        self._ctx = ctx
        self.invalidated = 0
        # The real window carries this, and the repaint fan-out reads it: the
        # scope coalescer keeps its dirty set on ``PlotState``. A stub without
        # it does not model the app, it models what the code happened to touch
        # on the day it was written.
        self._app_state = ctx.state

    def _invalidate_stats_cache(self):
        self.invalidated += 1
        self._ctx.state.stats_cache.invalidate()

    def _rebuild_all(self, *, invalidate=True):
        from review.core.selection_controller import rebuild_all
        rebuild_all(self._ctx, invalidate=invalidate)

    # Everything rebuild_all fans out to, stubbed to no-ops.
    def _groups_centre_refresh(self): pass
    def _refresh_sidebar_map(self): pass
    def _is_tab_visible(self, name): return False
    def _redraw(self): pass


class _CtxWithWindow(_Ctx):
    def __init__(self):
        super().__init__()
        self._window = _Window(self)
        self.features = None
        # rebuild_all schedules a debounced sample-definitions autosave; the
        # registry no-ops without a data dir, but the attribute has to exist.
        self.persistence = None

    @property
    def window(self):
        return self._window


def test_toggling_a_group_through_the_real_path_keeps_the_statistics(monkeypatch):
    """The assertion the earlier version of this file was missing.

    Reverting ``invalidate=False`` fails here with the cache emptied, which is
    what the branch did before this was noticed.
    """
    monkeypatch.setattr(
        "review.core.plot_toggles.refresh_fov_btn_state", lambda ctx: None)
    ctx = _CtxWithWindow()
    before = _fill(ctx)
    sels = [{"name": "G1", "wells": ["A01"], "hidden": False}]
    _set_groups_hidden(ctx, sels, target=True)

    assert sels[0]["hidden"] is True, "precondition: the toggle actually flipped"
    assert ctx.state.stats_cache.cache == before, (
        "toggling a group's visibility discarded the plate's statistics — the "
        "refresh invalidated them a line after they were deliberately kept"
    )
    assert ctx.window.invalidated == 0, (
        "the visibility path reached _invalidate_stats_cache"
    )


def test_a_toggle_that_changes_nothing_does_not_refresh_at_all(monkeypatch):
    """``target`` already satisfied: no flip, so no work of any kind."""
    monkeypatch.setattr(
        "review.core.plot_toggles.refresh_fov_btn_state", lambda ctx: None)
    ctx = _CtxWithWindow()
    before = _fill(ctx)
    sels = [{"name": "G1", "wells": ["A01"], "hidden": True}]
    _set_groups_hidden(ctx, sels, target=True)
    assert ctx.state.stats_cache.cache == before
    assert ctx.window.invalidated == 0
