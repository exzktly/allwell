"""A plot tab nobody is looking at is not repainted on every selection change.

``plot_orchestrator.redraw`` drives Line, Distribution and Heat Map. Its guard on
the two lazy tabs asked whether the tab had ever been *built*, not whether anyone
was looking at it — and once built it stays built, so a user who had opened both
paid for all three figures on every selection change for the rest of the session.
On a plate-wide selection each of those is over a second of matplotlib.

Skipping a figure is only safe if it is certain to be repainted before anyone
sees it, and "certain" here has to mean driving the real control rather than
calling the redraw function directly — that is the difference between asserting
a tab *can* be refreshed and asserting the app refreshes it.

These are full-app tests (the ``loaded_app`` fixture), so they are slow-lane.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def _show_leaf(app, name: str) -> bool:
    """Switch to a plotting leaf the way the sub-tab strip does.

    Line Graphs / Distribution / Heat Map are pages of a nested notebook inside
    the top-level "Plotting" tab, so ``_notebook.setCurrentByName`` cannot reach
    them — it only knows the top-level names.
    """
    if not app._notebook.setCurrentByName("Plotting"):
        return False
    nb = getattr(app, "_plotting_notebook", None)
    return bool(nb is not None and nb.setCurrentByName(name))


def _build_distribution(app):
    """Force the lazy Distribution tab to build, and report whether it did."""
    if not _show_leaf(app, "Distribution"):
        return False
    return app._ctx.widgets.has("_distribution_canvas")


def test_a_hidden_distribution_is_marked_dirty_rather_than_redrawn(loaded_app):
    app = loaded_app
    if not _build_distribution(app):
        pytest.skip("Distribution tab did not build in this profile")
    # Look at Line Graphs, not Distribution.
    assert _show_leaf(app, "Line Graphs")
    app._ctx.state.plot.fc_dirty_scopes.discard("distribution")

    calls = []
    orig = app._ctx.features.redraw

    def spy(ctx, name):
        calls.append(name)
        return orig(ctx, name)

    app._ctx.features.redraw = spy
    try:
        app._redraw()
    finally:
        app._ctx.features.redraw = orig

    assert "Distribution" not in calls, (
        "a Distribution tab nobody is looking at was repainted anyway"
    )
    assert "distribution" in app._ctx.state.plot.fc_dirty_scopes, (
        "the skipped figure was not marked dirty, so nothing will repaint it"
    )


def test_showing_the_tab_repaints_it(loaded_app):
    """The other half — and driven through the tab switch, not the redraw call.

    A test that called ``ctx.features.redraw`` directly would prove the figure
    *can* be refreshed while the app quietly never does it.
    """
    app = loaded_app
    if not _build_distribution(app):
        pytest.skip("Distribution tab did not build in this profile")
    assert _show_leaf(app, "Line Graphs")
    app._redraw()
    assert "distribution" in app._ctx.state.plot.fc_dirty_scopes

    calls = []
    orig = app._ctx.features.redraw

    def spy(ctx, name):
        calls.append(name)
        return orig(ctx, name)

    app._ctx.features.redraw = spy
    try:
        assert _show_leaf(app, "Distribution")
    finally:
        app._ctx.features.redraw = orig

    assert "Distribution" in calls, (
        "showing the tab did not repaint the figure that was skipped while it "
        "was hidden — the user is looking at a stale plot"
    )
    assert "distribution" not in app._ctx.state.plot.fc_dirty_scopes, (
        "the figure was repainted but is still marked dirty"
    )


def test_a_visible_distribution_is_still_redrawn(loaded_app):
    """Narrowing the fan-out must not narrow it for the tab in front of you."""
    app = loaded_app
    if not _build_distribution(app):
        pytest.skip("Distribution tab did not build in this profile")
    assert _show_leaf(app, "Distribution")

    calls = []
    orig = app._ctx.features.redraw

    def spy(ctx, name):
        calls.append(name)
        return orig(ctx, name)

    app._ctx.features.redraw = spy
    try:
        app._redraw()
    finally:
        app._ctx.features.redraw = orig

    assert "Distribution" in calls, "the visible tab was skipped"
