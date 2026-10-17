"""Toggling SD/SEM or Spread: FOV repaints every plot, not a named list of two.

Both toggles change the number every plot draws — the error bar's length, and
what counts as a replicate — so every plot has to be told. They were telling
three: ``redraw_app`` covers Line, Distribution and Heat Map, and a hardcoded
"and redraw Bar Plots if it is visible" covered the fourth. Response Curves is
a registered redraw scope like Bar, and was in neither, so clicking SEM with it
on screen flipped the state, repainted three figures nobody was looking at, and
left the curves in front of the reader drawn to the old error band — until they
left the tab and came back, which is the repaint ``on_activate`` was quietly
doing for them.

The assertion is on which redraws actually ran, not on ``use_sem``: a toggle
that writes its state perfectly and repaints nothing is exactly the failure,
and every state-level assertion passes right through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from review.core import plot_toggles
from review.core.plot_orchestrator import redraw_every_scope
from review.core.app_state import AppState
from review.core.scope_registry import redraw_app_scope
from review.domain.fold_change_scopes import (
    _REGISTERED_SCOPES, FoldChangeScope, register_fold_change_scope,
)


@dataclass
class _App:
    """Only what the coalescer and the toggles read off the window."""

    current_tab: str = "Response Curves"
    redraws: list[str] = field(default_factory=list)
    _app_state: AppState = field(default_factory=AppState)

    def _current_centre_tab(self) -> str:
        return self.current_tab


def _record(name):
    def _redraw(app):
        app.redraws.append(name)
    return _redraw


@pytest.fixture
def scopes():
    """The three shared-callable scopes plus the two feature ones.

    ``line`` / ``distribution`` / ``heatmap`` share ``redraw_app_scope`` exactly
    as ``core/scope_registry`` registers them, because "several scopes are one
    callable" is the fact the skip argument turns on.
    """
    snapshot = dict(_REGISTERED_SCOPES)
    _REGISTERED_SCOPES.clear()
    for name, tab in (("line", "Line Graphs"), ("distribution", "Distribution"),
                      ("heatmap", "Heat Map")):
        register_fold_change_scope(FoldChangeScope(
            name=name, ctrl_combo_attr="", baseline_combo_attr="",
            redraw=redraw_app_scope, tab_names=(tab,)))
    for name, tab in (("bar", "Bar Plots"), ("response", "Response Curves")):
        register_fold_change_scope(FoldChangeScope(
            name=name, ctrl_combo_attr="", baseline_combo_attr="",
            redraw=_record(name), tab_names=(tab,)))
    try:
        yield
    finally:
        _REGISTERED_SCOPES.clear()
        _REGISTERED_SCOPES.update(snapshot)


@pytest.fixture
def ctx(monkeypatch, scopes):
    """A ctx whose ``redraw_app`` is recorded rather than run."""
    from types import SimpleNamespace

    app = _App()
    calls = app.redraws
    import review.core.plot_orchestrator as po
    monkeypatch.setattr(po, "redraw_app",
                        lambda _ctx: calls.append("redraw_app"))
    return SimpleNamespace(window=app, state=app._app_state, redraws=calls)


def test_the_visible_scope_is_repainted(ctx):
    """Response Curves on screen: its scope redraws, in this click."""
    redraw_every_scope(ctx)
    assert "response" in ctx.redraws, (
        "the tab the user is looking at was not repainted after an error-band "
        "change, so its figure still shows the previous error band")


def test_a_hidden_scope_is_deferred_rather_than_forgotten(ctx):
    """Bar is not on screen, so it is marked dirty for the tab-change flush."""
    redraw_every_scope(ctx)
    assert "bar" not in ctx.redraws
    assert "bar" in ctx.state.plot.fc_dirty_scopes


def test_the_shared_scopes_are_drawn_once_and_left_clean(ctx):
    """``redraw_app`` already drew Line, Distribution and Heat Map.

    Without the skip they would be drawn again — and Line's figure can carry 96
    series, so "one more redraw" is a visible stall on every click. Marking
    them dirty instead would be as wrong in the other direction: they are
    clean, and a dirty mark would repaint them a second time on the next tab
    switch.
    """
    ctx.window.current_tab = "Line Graphs"
    redraw_every_scope(ctx)
    assert ctx.redraws.count("redraw_app") == 1
    dirty = ctx.state.plot.fc_dirty_scopes
    assert not ({"line", "distribution", "heatmap"} & dirty)


def test_the_sem_button_itself_reaches_the_visible_scope(ctx, monkeypatch):
    """Driven through ``toggle_sem``, which is what the button is wired to.

    Calling the fan-out directly would test the fan-out; the defect was that
    the button did not reach it. ``invalidate_threshold_results`` is stubbed because
    it walks widget banks this ctx does not have — the cache flush is not what
    is under test here.
    """
    monkeypatch.setattr(plot_toggles, "invalidate_threshold_results", lambda _c: None)
    ctx.runtime = _Runtime()
    was = ctx.state.plot.use_sem

    plot_toggles.toggle_sem(ctx)

    assert ctx.state.plot.use_sem is not was      # the state flipped…
    assert "response" in ctx.redraws              # …and the figure was redrawn


class _Bank:
    btns = None
    btn = None
    observers = None


class _Runtime:
    def __init__(self):
        self.sem = _Bank()
        self.fov = _Bank()
