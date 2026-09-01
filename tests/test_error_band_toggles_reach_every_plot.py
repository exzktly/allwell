"""The SD/SEM and Spread: FOV toggles repaint whatever plot is on screen.

Reported: "toggling sd and SEM has no effect on response curves".

The state was never the problem — ``collect_response`` reads
``ctx.state.plot.use_sem`` and ``use_fov_replicates``, and the button flipped
both correctly. Nothing repainted. ``toggle_sem`` and
``toggle_fov_replicates`` called ``plot_orchestrator.redraw_app``, which fans
out over Line, Distribution and Heat Map, plus one hardcoded
``if is_tab_visible(ctx, "Bar Plots")`` branch — four tabs out of nine, named
by string. Response Curves, Scatter, Multivariate and Spatial were not among
them, so on those four the control looked dead: the figure in front of it never
redrew, and leaving the tab and coming back is what "fixed" it.

Both toggles now announce on the bus, which is what ``plot_options_changed``
exists for — the ``EventBus`` docstring names "error bars" in that signal's own
description. ``RedrawCoordinator`` turns the emit into
``redraw_scopes_or_defer``: every visible scope now, the rest marked dirty for
the next tab switch.

**This drives the app's real sequence rather than either end of it** (§7.7 #1).
A test that asserted ``toggle_sem`` emits would pass against an emit nothing
listens for, and a test that called ``redraw_scopes_or_defer`` directly would
pass against the code that shipped the bug — it was never the coalescer that
was wrong. So the bus here is a real ``EventBus`` wired to the real coalescer
exactly as ``RedrawCoordinator.__init__`` wires it, and the assertion is on
which figures were repainted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from well_viewer.core.app_state import AppState
from well_viewer.core.event_bus import EventBus
from well_viewer.domain.fold_change_scopes import (
    _REGISTERED_SCOPES, FoldChangeScope, redraw_scopes_or_defer,
    register_fold_change_scope,
)


@dataclass
class _App:
    """The slice the coalescer reads: which tab is on screen, plus state."""

    current_tab: str = "Response Curves"
    redraws: list[str] = field(default_factory=list)
    _app_state: AppState = field(default_factory=AppState)

    def _current_centre_tab(self) -> str:
        return self.current_tab


#: Every plot leaf that has an error band to redraw, with the scope name it
#: registers under. Response, Scatter and Multivariate are the ones the old
#: fan-out could not reach; Line and Bar are here so a change that fixes the
#: first three by breaking the last two fails rather than reads as progress.
_LEAVES = {
    "response": "Response Curves",
    "scatter": "Scatter Plot",
    "multivariate": "Multivariate",
    "line": "Line Graphs",
    "bar": "Bar Plots",
}


@pytest.fixture
def scopes():
    """Register one recording scope per plot leaf, restoring the real ones after."""
    snapshot = dict(_REGISTERED_SCOPES)
    _REGISTERED_SCOPES.clear()
    for name, tab in _LEAVES.items():
        register_fold_change_scope(FoldChangeScope(
            name=name,
            ctrl_combo_attr=f"_fc_ctrl_combo_{name}",
            baseline_combo_attr=f"_fc_baseline_combo_{name}",
            redraw=(lambda app, n=name: app.redraws.append(n)),
            tab_names=(tab,),
        ))
    try:
        yield
    finally:
        _REGISTERED_SCOPES.clear()
        _REGISTERED_SCOPES.update(snapshot)


@pytest.fixture
def wired(scopes, monkeypatch):
    """A ctx whose bus is connected the way ``RedrawCoordinator`` connects it."""
    app = _App()
    bus = EventBus()
    bus.plot_options_changed.connect(lambda _s=None: redraw_scopes_or_defer(app))

    import well_viewer.core.plot_toggles as pt
    # The bar-layout button is a lazy proxy into the bar feature and is not what
    # this file is about; the *state* and the *fan-out* are.
    monkeypatch.setattr(pt, "_refresh_bar_layout_btn_state", lambda _c: None)

    ctx = SimpleNamespace(
        state=app._app_state,
        bus=bus,
        runtime=SimpleNamespace(
            sem=SimpleNamespace(btns=[], btn=None, observers=[]),
            fov=SimpleNamespace(btns=[], btn=None, observers=[]),
            # ``invalidate_stats_cache`` cues these two on the way past. They
            # are plain banks and callback slots, not the thing under test, but
            # they are spelled out rather than stubbed away so the toggle runs
            # the real body — the cue that raises here would abort the redraw
            # after the state had already flipped, which is a bug this file
            # would otherwise be blind to.
            image_table=SimpleNamespace(inclusion_changed_cb=None),
            review_image=None,
        ),
    )
    return SimpleNamespace(ctx=ctx, app=app)


@pytest.mark.parametrize("tab, scope", sorted(
    (tab, scope) for scope, tab in _LEAVES.items()))
def test_the_sem_toggle_repaints_whichever_plot_is_on_screen(wired, tab, scope):
    from well_viewer.core.plot_toggles import toggle_sem

    wired.app.current_tab = tab
    toggle_sem(wired.ctx)

    assert wired.app.redraws == [scope], (
        f"with {tab} on screen the SD/SEM toggle repainted "
        f"{wired.app.redraws or 'nothing'}")


@pytest.mark.parametrize("tab, scope", sorted(
    (tab, scope) for scope, tab in _LEAVES.items()))
def test_the_fov_toggle_repaints_whichever_plot_is_on_screen(wired, tab, scope):
    from well_viewer.core.plot_toggles import toggle_fov_replicates

    wired.app.current_tab = tab
    toggle_fov_replicates(wired.ctx)

    assert wired.app.redraws == [scope], (
        f"with {tab} on screen the Spread: FOV toggle repainted "
        f"{wired.app.redraws or 'nothing'}")


def test_the_toggle_still_flips_the_state_it_is_named_for(wired):
    """The half that always worked, pinned so a fan-out fix cannot lose it."""
    from well_viewer.core.plot_toggles import toggle_sem, toggle_fov_replicates

    before_sem = bool(wired.ctx.state.plot.use_sem)
    toggle_sem(wired.ctx)
    assert bool(wired.ctx.state.plot.use_sem) is not before_sem

    before_fov = bool(wired.ctx.state.plot.use_fov_replicates)
    toggle_fov_replicates(wired.ctx)
    assert bool(wired.ctx.state.plot.use_fov_replicates) is not before_fov


def test_a_hidden_plot_is_deferred_and_flushes_when_it_is_looked_at(wired):
    """The coalescer's contract, which the fix relies on and must not lose.

    Redrawing every scope on every click would repaint eight figures nobody is
    looking at. A hidden scope is marked dirty instead and redrawn by
    ``flush_dirty_scopes`` on the tab change that makes it visible — and only
    then, which is the half worth pinning: a flush that drew every dirty scope
    would have thrown the saving away.
    """
    from well_viewer.domain.fold_change_scopes import flush_dirty_scopes
    from well_viewer.core.plot_toggles import toggle_sem

    wired.app.current_tab = "Response Curves"
    toggle_sem(wired.ctx)
    assert wired.app.redraws == ["response"]

    wired.app.current_tab = "Bar Plots"
    flush_dirty_scopes(wired.app)
    assert wired.app.redraws == ["response", "bar"], (
        "the tab switch should flush the newly-visible scope and nothing else")

    wired.app.current_tab = "Multivariate"
    flush_dirty_scopes(wired.app)
    assert wired.app.redraws == ["response", "bar", "multivariate"]

    # Idempotent: a scope that has flushed is no longer dirty.
    flush_dirty_scopes(wired.app)
    assert wired.app.redraws == ["response", "bar", "multivariate"]
