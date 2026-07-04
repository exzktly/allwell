"""Redraw fan-out regression harness.

Counts actual invocations of each fold-change scope's redraw callable
(``redraw_app`` for the line scope, ``redraw_bars`` for the bar scope) on
a *real* ``WellViewerApp``. A single user action must produce **exactly
one** redraw of the visible scope — never zero, never two.

This guards Phase 3 of the ``runtime_app.py`` teardown. When the ``EventBus``
becomes load-bearing, a bus subscriber must *replace* the direct redraw call,
not run in addition to it. A regression that leaves both code paths live (the
"additive" failure mode) shows up here as a fan-out count of 2. The pure-logic
``test_fold_change_scopes.py`` cannot catch that — it mocks the coalescer and
never exercises the bus — so this real-app harness is the safety net.

Baseline (pre-Phase-3, additive emits with no subscribers): one redraw per
visible scope per action; the bus emit goes nowhere.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.domain.fold_change_controls import set_fold_change_state
from well_viewer.domain.fold_change_scopes import (
    get_scope, register_fold_change_scope,
)

pytestmark = pytest.mark.qt


class _RedrawCounter:
    """Tally every invocation of the line / bar scope redraw callables.

    The bar scope only enters through the scope coalescer, so wrapping
    the scope's callable is enough. The line scope enters through TWO
    paths: the coalescer (channel / fold-change) and the
    ``dataset_loaded`` subscriber (which calls ``app._redraw`` directly
    for a full post-load redraw). Wrap both so every effective redraw
    is observed exactly once. Wrappers call through to the originals so
    the app behaves normally; ``restore()`` undoes both wrappings.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.counts = {"line": 0, "bar": 0}

        self._original_scopes = {}
        for name in ("line", "bar"):
            scope = get_scope(name)
            assert scope is not None, f"scope {name!r} not registered"
            self._original_scopes[name] = scope
            register_fold_change_scope(replace(
                scope, redraw=self._wrap(name, scope.redraw),
            ))

        self._original_app_redraw = app._redraw
        app._redraw = self._wrap("line", app._redraw)

    def _wrap(self, name, original):
        def _counted(*args, **kwargs):
            self.counts[name] += 1
            return original(*args, **kwargs)
        return _counted

    def reset(self) -> None:
        self.counts = {"line": 0, "bar": 0}

    def restore(self) -> None:
        for scope in self._original_scopes.values():
            register_fold_change_scope(scope)
        self.app._redraw = self._original_app_redraw

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _show_centre_tab(app, leaf: str) -> None:
    """Make ``leaf`` (a plotting sub-tab) the visible centre tab."""
    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName(leaf)
    assert app._current_centre_tab() == leaf


@pytest.fixture
def counted_app(loaded_app):
    """A loaded app plus a redraw counter wired onto its scope methods."""
    counter = _RedrawCounter(loaded_app)
    try:
        yield loaded_app, counter
    finally:
        counter.restore()


# ── Channel change ───────────────────────────────────────────────────────────

def test_channel_change_redraws_visible_bar_scope_once(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Bar Plots")
    counter.reset()

    set_active_channel(app._ctx, "mcherry")

    assert counter.counts["bar"] == 1, "visible bar scope must redraw exactly once"
    assert counter.counts["line"] == 0, "hidden line scope must defer (no redraw)"
    assert counter.total == 1, "no fan-out: one action -> one redraw"


def test_channel_change_redraws_visible_line_scope_once(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Line Graphs")
    counter.reset()

    set_active_channel(app._ctx, "mcherry")

    assert counter.counts["line"] == 1, "visible line scope must redraw exactly once"
    assert counter.counts["bar"] == 0, "hidden bar scope must defer (no redraw)"
    assert counter.total == 1, "no fan-out: one action -> one redraw"


# ── Fold-change toggle ───────────────────────────────────────────────────────

def test_fold_change_toggle_redraws_visible_scope_once(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Bar Plots")
    counter.reset()

    set_fold_change_state(app, vs_t0_on=True)

    assert counter.counts["bar"] == 1, "fold-change toggle must redraw the bar scope once"
    assert counter.counts["line"] == 0, "hidden line scope must defer"
    assert counter.total == 1, "no fan-out: one action -> one redraw"


# ── Dataset load ─────────────────────────────────────────────────────────────

def test_dataset_loaded_signal_redraws_visible_bar_scope(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Bar Plots")
    counter.reset()

    app._event_bus.dataset_loaded.emit(app._app_state.dataset)

    # _redraw (the line-scope method) covers line / distribution / heat map.
    # With Bar Plots the visible tab, the bar scope must also draw on load
    # (otherwise it's an empty white axis) — exactly once, no fan-out.
    assert counter.counts["line"] == 1, "dataset load must drive exactly one _redraw"
    assert counter.counts["bar"] == 1, "visible bar scope must draw on load"


def test_dataset_loaded_does_not_double_draw_line_scope(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Line Graphs")
    counter.reset()

    app._event_bus.dataset_loaded.emit(app._app_state.dataset)

    # The line scope shares _redraw's callable, so when the line tab is visible
    # the post-load scope pass must NOT redraw it again (would be a fan-out of 2).
    assert counter.counts["line"] == 1, "line scope must draw exactly once on load"
    assert counter.counts["bar"] == 0, "hidden bar scope defers (marked dirty)"


# ── Deferred-scope flush on tab switch ───────────────────────────────────────

def test_deferred_scope_flushes_exactly_once_on_tab_switch(counted_app):
    app, counter = counted_app

    # Act on the bar scope while line is hidden -> line is marked dirty.
    _show_centre_tab(app, "Bar Plots")
    counter.reset()
    set_active_channel(app._ctx, "mcherry")
    assert counter.counts["line"] == 0, "hidden line scope must not redraw eagerly"

    # Switching to the line tab flushes the one deferred redraw — and only one.
    counter.reset()
    _show_centre_tab(app, "Line Graphs")
    assert counter.counts["line"] == 1, "deferred line scope must flush exactly once"
    assert counter.total == 1, "tab switch must not fan out redraws"
