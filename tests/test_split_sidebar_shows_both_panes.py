"""Both panes of a Split View get their sidebar section, not just the focused one.

Reported against Plots beside Statistics: a per-tab sidebar section did not
appear until the user clicked into its pane. The click bought nothing — they
were already looking at the tab the panel belongs to.

``on_tab_change`` hides every per-tab sidebar frame and then runs ``on_activate``
for the tab ``current_centre_tab`` reports. In Split View that resolver returns
the *focused* leaf, so the other pane's frame was hidden by the first half and
never shown by the second — visible pane, invisible controls.

The pair here is Plots beside Image Browser, parked on Segmentation. Statistics
no longer has a rail section to be hidden: its comparison-group editor is part
of the Statistics page, which is what
``tests/test_stats_groups_need_no_focus.py`` covers. The rule this file states
outlives that change — three sidebar sections are still shown or hidden per
visible pane — and a rule tested only through the one tab that stopped
exercising it is a rule with no test at all.

Slow lane on purpose: this is about two real panes, a real sidebar and the real
activation dispatch, and every cheaper seam is underneath the thing that was
broken.

**This file asserts that the sections are shown; it does not assert what is in
them, and that gap shipped a regression.** Three sidebar sections build their
own well picker, so showing two of them put two pickers on the rail — which
every assertion here passes happily, because a visibility flag is not a picker.
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


def _park_on_segmentation(app):
    """Segmentation is a leaf inside the Image Browser container, and it is the
    leaf that owns ``_sidebar_preview_frame``."""
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app._ctx, "Segmentation")


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
def test_the_other_pane_s_section_shows_without_clicking_it(loaded_app):
    """The reported case, with Plots focused — the state you land in when you
    open the split from the Plots side."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    tabs = _split(app, "Plotting", "Image Browser")
    assert "Segmentation" in tabs

    assert _visible(app, "_sidebar_preview_frame"), (
        "the Segmentation controls stayed hidden while their pane was on "
        "screen — the user had to click the pane to reveal them")


@pytest.mark.slow
def test_the_focused_pane_keeps_its_own_section(loaded_app):
    """The other half of the rule. Showing the second pane's section must not
    cost the focused pane its own."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")
    assert _visible(app, "_sidebar_main_frame")
    assert _visible(app, "_sidebar_preview_frame")


@pytest.mark.slow
def test_it_holds_with_the_panes_the_other_way_round(loaded_app):
    """Focus lands on pane A, so a rule written only for "the second one" would
    pass one ordering and fail the other."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Image Browser")
    _split(app, "Image Browser", "Plotting")
    assert _visible(app, "_sidebar_preview_frame")
    assert _visible(app, "_sidebar_main_frame")


@pytest.mark.slow
def test_leaving_split_puts_the_sidebar_back_to_one_section(loaded_app):
    """The frames are hidden by ``on_tab_change`` and shown per visible pane, so
    exiting has to leave exactly the single-pane sidebar behind — a Segmentation
    section still showing over the Plots tab would be the mirror bug."""
    from well_viewer.core.split_view import exit_split

    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")
    assert _visible(app, "_sidebar_preview_frame")

    exit_split(app._ctx)
    app._notebook.setCurrentByName("Plotting")

    assert not _visible(app, "_sidebar_preview_frame"), (
        "the Segmentation section outlived the pane it belongs to")
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
