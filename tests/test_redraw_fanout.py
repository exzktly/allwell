"""Redraw fan-out regression harness.

Counts actual invocations of the scope redraw methods (``_redraw`` for the
line scope, ``_redraw_bars`` for the bar scope) on a *real* ``WellViewerApp``.
A single user action must produce **exactly one** redraw of the visible
scope — never zero, never two.

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

import pytest

from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.domain.fold_change_controls import set_fold_change_state

pytestmark = pytest.mark.qt


class _RedrawCounter:
    """Wrap the scope redraw methods on a real app and tally invocations.

    The wrappers call through to the originals so the app behaves normally —
    only the call count is observed.
    """

    _METHODS = {"line": "_redraw", "bar": "_redraw_bars"}

    def __init__(self, app) -> None:
        self.app = app
        self.counts = {"line": 0, "bar": 0}
        for scope, method in self._METHODS.items():
            setattr(app, method, self._wrap(scope, getattr(app, method)))

    def _wrap(self, scope, original):
        def _counted(*args, **kwargs):
            self.counts[scope] += 1
            return original(*args, **kwargs)
        return _counted

    def reset(self) -> None:
        self.counts = {"line": 0, "bar": 0}

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
    return loaded_app, _RedrawCounter(loaded_app)


# ── Channel change ───────────────────────────────────────────────────────────

def test_channel_change_redraws_visible_bar_scope_once(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Bar Plots")
    counter.reset()

    set_active_channel(app, "mcherry")

    assert counter.counts["bar"] == 1, "visible bar scope must redraw exactly once"
    assert counter.counts["line"] == 0, "hidden line scope must defer (no redraw)"
    assert counter.total == 1, "no fan-out: one action -> one redraw"


def test_channel_change_redraws_visible_line_scope_once(counted_app):
    app, counter = counted_app
    _show_centre_tab(app, "Line Graphs")
    counter.reset()

    set_active_channel(app, "mcherry")

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

def test_dataset_loaded_signal_redraws_once(counted_app):
    app, counter = counted_app
    counter.reset()

    app._event_bus.dataset_loaded.emit(app._app_state.dataset)

    # _redraw (the line-scope method) covers line / distribution / heat map —
    # the full post-load redraw. Bars redraw lazily on tab entry.
    assert counter.counts["line"] == 1, "dataset load must drive exactly one _redraw"
    assert counter.counts["bar"] == 0


# ── Deferred-scope flush on tab switch ───────────────────────────────────────

def test_deferred_scope_flushes_exactly_once_on_tab_switch(counted_app):
    app, counter = counted_app

    # Act on the bar scope while line is hidden -> line is marked dirty.
    _show_centre_tab(app, "Bar Plots")
    counter.reset()
    set_active_channel(app, "mcherry")
    assert counter.counts["line"] == 0, "hidden line scope must not redraw eagerly"

    # Switching to the line tab flushes the one deferred redraw — and only one.
    counter.reset()
    _show_centre_tab(app, "Line Graphs")
    assert counter.counts["line"] == 1, "deferred line scope must flush exactly once"
    assert counter.total == 1, "tab switch must not fan out redraws"
