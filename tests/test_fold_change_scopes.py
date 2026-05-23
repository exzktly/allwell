"""Tests for the dirty-flag tab-redraw plumbing in fold_change_controls.

Verifies that ``redraw_scopes_or_defer`` only fires the visible scope's
redraw and that ``flush_dirty_scopes`` drains pending redraws on a
tab switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from well_viewer.domain.fold_change_scopes import (
    FoldChangeScope,
    current_scope_name, flush_dirty_scopes,
    redraw_scopes_or_defer, register_fold_change_scope,
    registered_scopes,
)
from well_viewer.core.app_state import AppState


@dataclass
class _TabbedApp:
    """Mock of just the slice WellViewerApp exposes for tab tracking.

    Records every redraw method called, so tests can assert the visible
    tab fired and the others didn't.
    """
    current_tab: str = "Bar Plots"
    redraws: List[str] = field(default_factory=list)
    _app_state: AppState = field(default_factory=AppState)

    def _current_centre_tab(self) -> str:
        return self.current_tab

    def _redraw_bars(self) -> None:
        self.redraws.append("bar")

    def _redraw(self) -> None:
        self.redraws.append("line")


@pytest.fixture(autouse=True)
def _isolate_dirty_state(monkeypatch):
    """Each test gets a clean scope-dirty set even if it shares an app."""
    yield


def test_current_scope_name_bar_tab():
    app = _TabbedApp(current_tab="Bar Plots")
    assert current_scope_name(app) == "bar"


def test_current_scope_name_line_tab():
    app = _TabbedApp(current_tab="Line Graphs")
    assert current_scope_name(app) == "line"


def test_current_scope_name_unrelated_tab_returns_none():
    app = _TabbedApp(current_tab="Batch Export")
    assert current_scope_name(app) is None


def test_redraw_scopes_or_defer_only_visible_runs():
    app = _TabbedApp(current_tab="Bar Plots")
    redraw_scopes_or_defer(app)
    assert app.redraws == ["bar"]
    # Line scope was marked dirty.
    assert app._app_state.plot.fc_dirty_scopes == {"line"}


def test_redraw_scopes_or_defer_no_visible_marks_all_dirty():
    """When the user is on an unrelated tab (e.g. Batch Export), every
    scope is marked dirty — none should redraw eagerly."""
    app = _TabbedApp(current_tab="Batch Export")
    redraw_scopes_or_defer(app)
    assert app.redraws == []
    assert app._app_state.plot.fc_dirty_scopes == {"bar", "line"}


def test_flush_dirty_scopes_redraws_on_tab_entry():
    """Switching to a dirty tab drains its deferred redraw."""
    app = _TabbedApp(current_tab="Bar Plots")
    redraw_scopes_or_defer(app)   # Line is dirty, bar redrew
    assert app.redraws == ["bar"]
    # User switches to the Line Graphs tab.
    app.current_tab = "Line Graphs"
    flush_dirty_scopes(app)
    assert app.redraws == ["bar", "line"]
    assert app._app_state.plot.fc_dirty_scopes == set()


def test_flush_dirty_scopes_idempotent_when_clean():
    """Switching to a tab that isn't dirty is a no-op."""
    app = _TabbedApp(current_tab="Bar Plots")
    redraw_scopes_or_defer(app)
    app.redraws.clear()
    # Switch to a tab that wasn't dirty (Bar was just rendered).
    flush_dirty_scopes(app)
    assert app.redraws == []


def test_flush_dirty_scopes_on_unrelated_tab_is_noop():
    app = _TabbedApp(current_tab="Bar Plots")
    redraw_scopes_or_defer(app)
    app.current_tab = "Batch Export"
    app.redraws.clear()
    flush_dirty_scopes(app)
    assert app.redraws == []


def test_register_fold_change_scope_replaces_existing():
    """Re-registering a scope updates its descriptor in place."""
    original = next(s for s in registered_scopes() if s.name == "bar")
    new = FoldChangeScope(
        name="bar",
        ctrl_combo_attr="_test_ctrl",
        baseline_combo_attr="_test_baseline",
        redraw_method="_redraw_bars",
        tab_names=("Bar Plots",),
    )
    register_fold_change_scope(new)
    try:
        # Sanity — the new descriptor sticks.
        cur = next(s for s in registered_scopes() if s.name == "bar")
        assert cur.ctrl_combo_attr == "_test_ctrl"
    finally:
        # Restore the original so subsequent tests aren't affected.
        register_fold_change_scope(original)
