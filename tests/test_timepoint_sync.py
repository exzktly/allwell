"""Pure-logic tests for the shared focus-timepoint sync (N5)."""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core.timepoint_sync import (
    current_focus_tp,
    nearest_index,
    set_focus_tp,
)


def _ctx():
    # A8-teardown T3: the leaves take ``ctx`` and read ``ctx.state.plot`` /
    # ``ctx.bus`` — mirror that minimal surface here.
    emitted = []
    bus = SimpleNamespace(
        plot_options_changed=SimpleNamespace(emit=lambda s: emitted.append(s)))
    ctx = SimpleNamespace(
        state=SimpleNamespace(plot=SimpleNamespace(focus_timepoint=None)),
        bus=bus,
    )
    ctx._emitted = emitted
    return ctx


def test_nearest_index_picks_closest():
    assert nearest_index([0.0, 24.0, 48.0], 25.0) == 1
    assert nearest_index([0.0, 24.0, 48.0], 47.9) == 2
    assert nearest_index([0.0, 24.0, 48.0], 0.0) == 0


def test_nearest_index_handles_unset_and_empty():
    assert nearest_index([0.0, 24.0], None) is None
    assert nearest_index([], 5.0) is None
    assert nearest_index([0.0, 24.0], "bad") is None


def test_set_and_get_focus_tp_emits_once():
    ctx = _ctx()
    set_focus_tp(ctx, 24.0)
    assert current_focus_tp(ctx) == 24.0
    assert len(ctx._emitted) == 1


def test_set_focus_tp_unchanged_does_not_re_emit():
    ctx = _ctx()
    set_focus_tp(ctx, 24.0)
    set_focus_tp(ctx, 24.0)
    assert len(ctx._emitted) == 1


def test_set_focus_tp_emit_false_is_silent():
    ctx = _ctx()
    set_focus_tp(ctx, 12.0, emit=False)
    assert current_focus_tp(ctx) == 12.0
    assert ctx._emitted == []
