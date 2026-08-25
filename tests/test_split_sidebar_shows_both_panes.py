"""Both panes of a Split View get their sidebar section, not just the focused one.

Reported: with Plots and Statistics side by side, the Statistics group-definition
editor — which lives in the left sidebar under the well picker — did not appear
until the user clicked into the Statistics pane. The click bought nothing: they
were already looking at the tab the panel belongs to, and the panel is how you
define the groups the tab compares.

``on_tab_change`` hides every per-tab sidebar frame and then runs ``on_activate``
for the tab ``current_centre_tab`` reports. In Split View that resolver returns
the *focused* leaf, so the other pane's frame was hidden by the first half and
never shown by the second — visible pane, invisible controls.

Slow lane on purpose: this is about two real panes, a real sidebar and the real
activation dispatch, and every cheaper seam is underneath the thing that was
broken.

**This file asserts that the sections are shown; it does not assert what is in
them, and that gap shipped a regression.** Four sidebar sections build their own
well picker, so showing two of them put two pickers on the rail — which every
assertion here passes happily, because a visibility flag is not a picker.
``tests/test_split_sidebar_one_well_picker.py`` counts the pickers. Read the two
together: this one says the section is on screen, that one says the rail is not
duplicated.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")


def _split(app, tab_a, tab_b):
    from well_viewer.core.split_view import enter_split
    enter_split(app._ctx, tab_a, tab_b)
    return app._visible_centre_tabs()


def _visible(app, name) -> bool:
    """Whether *name*'s own show/hide flag is set.

    ``isHidden()``, not ``isVisible()``: the test window is never shown, so
    ``isVisible()`` is False for every widget in it whatever the dispatch did.
    The flag ``setVisible()`` writes is the state under test, and asserting the
    other one would have made this file pass on any implementation at all.
    """
    frame = app._ctx.widgets.get_or_none(name)
    return frame is not None and not frame.isHidden()


@pytest.mark.slow
def test_the_statistics_editor_shows_without_clicking_its_pane(loaded_app):
    """The reported case, with Plots focused — the state you land in when you
    open the split from the Plots side."""
    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    tabs = _split(app, "Plotting", "Statistics")
    assert "Statistics" in tabs

    assert _visible(app, "_sidebar_stats_frame"), (
        "the Statistics group editor stayed hidden while its pane was on "
        "screen — the user had to click the pane to reveal it")


@pytest.mark.slow
def test_the_focused_pane_keeps_its_own_section(loaded_app):
    """The other half of the rule. Showing the second pane's section must not
    cost the focused pane its own."""
    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")
    assert _visible(app, "_sidebar_main_frame")
    assert _visible(app, "_sidebar_stats_frame")


@pytest.mark.slow
def test_it_holds_with_the_panes_the_other_way_round(loaded_app):
    """Focus lands on pane A, so a rule written only for "the second one" would
    pass one ordering and fail the other."""
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    _split(app, "Statistics", "Plotting")
    assert _visible(app, "_sidebar_stats_frame")
    assert _visible(app, "_sidebar_main_frame")


@pytest.mark.slow
def test_leaving_split_puts_the_sidebar_back_to_one_section(loaded_app):
    """The frames are hidden by ``on_tab_change`` and shown per visible pane, so
    exiting has to leave exactly the single-pane sidebar behind — a Statistics
    editor still showing over the Plots tab would be the mirror bug."""
    from well_viewer.core.split_view import exit_split

    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")
    assert _visible(app, "_sidebar_stats_frame")

    exit_split(app._ctx)
    app._notebook.setCurrentByName("Plotting")

    assert not _visible(app, "_sidebar_stats_frame"), (
        "the Statistics section outlived the pane it belongs to")
    assert _visible(app, "_sidebar_main_frame")


@pytest.mark.slow
def test_the_unfocused_pane_does_not_take_the_plate_s_drag_mode(loaded_app):
    """Sidebar *frames* follow every visible pane; the plate's interaction mode
    does not. Drag mode changes what a plate click does, and the plate is
    shared, so following an unfocused Heat Map would rewire the clicks of the
    pane the user is actually working in.

    Heat Map is a leaf inside the Plotting container, so the split is
    Statistics + Plotting with the plotting notebook parked on Heat Map, and
    focus on Statistics — the arrangement that puts a Heat Map on screen without
    it being the focused leaf.
    """
    app = loaded_app
    assert app._plotting_notebook.setCurrentByName("Heat Map")
    app._notebook.setCurrentByName("Statistics")
    tabs = _split(app, "Statistics", "Plotting")
    assert "Heat Map" in tabs

    # The configurator frame belongs to a visible pane, so it shows...
    hm = app._ctx.widgets.get_or_none("_heatmap_sidebar_frame")
    if hm is not None:
        assert not hm.isHidden()
    # ...while the plate stays in the focused pane's mode.
    plate = app._ctx.widgets.get_or_none("_sidebar_plate")
    if plate is not None and hasattr(plate, "dragMime"):
        assert not plate.dragMime(), (
            "an unfocused Heat Map put the shared plate into drag-source mode")
