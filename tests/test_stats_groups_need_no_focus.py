"""Defining a comparison group takes no click into the Statistics pane.

Reported: in Split View with plots on the left and Statistics on the right, and
no replicate sets defined on Home, ``+ Add`` made a group with no members and
paid no attention to the wells picked on the plate. The user had to click the
Statistics pane first — a gesture unrelated to the task, on a panel they were
already looking at.

The cause was a mode on a shared instrument. The rail carries exactly one well
picker (``well_viewer/core/rail_plate.py``) and it follows the focused pane, so
the plate on screen was the shared one, which selects wells to plot. The plate
that paints wells into a comparison group belonged to the Statistics rail
section and was hidden. Two plates, identical to look at, opposite meanings, and
nothing on screen naming which was which — so ``+ Add`` created a group the
visible selection could never reach.

The fix is not a label. The comparison-group editor is part of the Statistics
*page*: Statistics contributes no rail section and no rail plate at all, so the
rail while a Statistics pane is on screen belongs unambiguously to the other
pane, and the plate that fills a group is inside the pane the user is working
in, beside the cards that name the group it fills.

Slow lane on purpose: this is about two real panes, the real rail and the real
activation dispatch, and every cheaper seam sits underneath what was broken.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")


def _split_plots_left_statistics_right(app):
    """The reported arrangement: Plots focused, Statistics beside it."""
    from well_viewer.core.split_view import enter_split
    app._notebook.setCurrentByName("Statistics")     # build the page
    app._notebook.setCurrentByName("Plotting")
    enter_split(app._ctx, "Plotting", "Statistics")
    assert "Statistics" in app._visible_centre_tabs()
    return app._ctx


def _split_statistics_beside_segmentation(app):
    """Statistics focused, beside a pane that *does* own a rail section.

    The partner matters and this file originally got it wrong. Plotting's
    section is ``_sidebar_main_frame``, which is also the fallback the rail's
    arbitration reaches for when the focused pane contributes nothing — so
    pairing Statistics with Plotting passes whether the rule holds or not. A
    partner with a section of its own is what separates "the picker went to the
    other pane" from "the picker happened to be the fallback".
    """
    from well_viewer.core.split_view import enter_split, focus_split
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app._ctx, "Segmentation")
    app._notebook.setCurrentByName("Statistics")
    enter_split(app._ctx, "Statistics", "Image Browser")
    focus_split(app._ctx, "Statistics")
    assert "Segmentation" in app._visible_centre_tabs()
    return app._ctx


def _rail_plates(ctx):
    """Every well picker mounted in the shared rail slot, hidden or not."""
    from widgets.well_plate_selector import WellPlateSelector
    slot = ctx.widgets.get_or_none("_sidebar_plate_slot")
    return [] if slot is None else list(slot.findChildren(WellPlateSelector))


@pytest.mark.slow
def test_the_group_plate_is_on_screen_with_the_other_pane_focused(loaded_app):
    """The reported symptom, stated as the thing the user could not see.

    ``isHidden()``, not ``isVisible()``: the test window is never shown, so
    ``isVisible()`` is False for everything in it and asserting it would pass on
    any implementation. The flag the old rail mount wrote — ``setVisible(False)``
    on every plate that is not the focused pane's — is the state under test.
    """
    app = loaded_app
    ctx = _split_plots_left_statistics_right(app)

    plate = ctx.widgets.get_or_none("_stats_map_plate")
    assert plate is not None, "the Statistics comparison-group plate was never built"
    assert not plate.isHidden(), (
        "the plate that paints wells into a comparison group was hidden while "
        "its pane was on screen — the user had to click the pane to reveal it")


@pytest.mark.slow
def test_the_group_plate_is_not_on_the_rail(loaded_app):
    """Where it is, not only that it is somewhere.

    Being on screen is not enough: mounted in the rail slot it would be on
    screen only for as long as Statistics held the focus, which is the bug.
    """
    app = loaded_app
    ctx = _split_plots_left_statistics_right(app)

    plate = ctx.widgets.get("_stats_map_plate")
    assert plate not in _rail_plates(ctx), (
        "the comparison-group plate is mounted in the shared rail slot, so its "
        "visibility is still a function of which pane has the focus")


@pytest.mark.slow
def test_painting_a_well_fills_the_group_without_a_focus_change(loaded_app):
    """The whole reported gesture, in one pass, with the focus never moving.

    Asserts the well that lands in the group, not merely that the group stopped
    being empty: "not empty" is satisfied by any implementation that puts
    *something* there, and the defect was a group that ignored the wells the
    user had picked.
    """
    from well_viewer.features.stats.controller import stats_grp_add

    app = loaded_app
    ctx = _split_plots_left_statistics_right(app)
    focused_before = ctx.runtime.split.focused

    ctx.state.stats.clear_groups()
    stats_grp_add(ctx)
    well = next(iter(ctx.state.dataset.well_paths))

    plate = ctx.widgets.get("_stats_map_plate")
    # The reachability assertion is part of the gesture, not decoration. Driven
    # programmatically the plate's wiring works whether or not it is on screen —
    # measured against the pre-fix tree the rest of this test passes — so
    # without this line the test would confirm a control the user cannot touch.
    assert not plate.isHidden(), (
        "the map the group is painted on is off screen, so this gesture is not "
        "one the user could perform")
    plate.setSelectedWellIds([well])
    plate.selectionDragFinished.emit()

    group = ctx.state.stats.active_group()
    assert group is not None and well in group.wells, (
        f"{well} was painted onto the comparison-group map and did not reach "
        f"the active group (it holds {sorted(group.wells) if group else None})")
    assert ctx.runtime.split.focused == focused_before, (
        "defining a group moved the focus — the click into the pane this "
        "change exists to remove")


@pytest.mark.slow
def test_statistics_puts_no_picker_on_the_rail(loaded_app):
    """The root cause, removed rather than arbitrated.

    Every plate still on the rail answers "which wells am I looking at" — the
    shared one selects wells to plot, Segmentation's picks the well to preview,
    the Image Table's picks the wells to tile. The comparison-group plate asked
    a different question in the same slot, and that is the mode this file is
    about. It is gone from the slot entirely, so there is nothing to arbitrate.
    """
    app = loaded_app
    ctx = _split_plots_left_statistics_right(app)

    from well_viewer.core.rail_plate import section_of
    stats_frame = ctx.widgets.get_or_none("_sidebar_stats_frame")
    assert stats_frame is None, (
        "Statistics still owns a rail section; its editor is meant to live on "
        "its own page")
    shown = [p for p in _rail_plates(ctx) if not p.isHidden()]
    assert len(shown) == 1, (
        f"{len(shown)} well pickers on the rail; expected the plot selector "
        f"alone (sections: {[section_of(p) for p in shown]})")
    assert shown[0] is ctx.widgets.get("_sidebar_plate"), (
        "the picker on the rail is not the shared plot selector")


@pytest.mark.slow
def test_focusing_statistics_leaves_the_rail_to_the_other_pane(loaded_app):
    """The other direction. With no section of its own, Statistics taking the
    focus must not blank the rail: the picker belongs to whatever else is on
    screen, and in this split that is the plots."""
    from well_viewer.core.split_view import focus_split

    app = loaded_app
    ctx = _split_plots_left_statistics_right(app)
    focus_split(ctx, "Statistics")

    shown = [p for p in _rail_plates(ctx) if not p.isHidden()]
    assert len(shown) == 1 and shown[0] is ctx.widgets.get("_sidebar_plate"), (
        "focusing the Statistics pane took the plot selector off the rail")
    assert not ctx.widgets.get("_stats_map_plate").isHidden(), (
        "the comparison-group plate went away when its pane took the focus")


@pytest.mark.slow
def test_a_sectionless_pane_hands_the_picker_over_rather_than_blanking_it(loaded_app):
    """The case the Plotting pairing above cannot see.

    A focused pane that contributes no sidebar section has no claim on the one
    rail picker, and the arbitration must pass it to the visible pane that does
    — not to the shared frame, which is hidden here and leaves the rail with no
    picker at all. Measured before the fix: the rail carried Segmentation's
    section, its "Click one well to load its images" line, and nothing to click.
    That is this pane's version of the defect the whole change is about.
    """
    from well_viewer.core.rail_plate import section_of

    app = loaded_app
    ctx = _split_statistics_beside_segmentation(app)

    shown = [p for p in _rail_plates(ctx) if not p.isHidden()]
    assert len(shown) == 1, (
        f"{len(shown)} well pickers on the rail while the Segmentation pane is "
        f"on screen with its section shown; it should have exactly its own")
    assert section_of(shown[0]) is ctx.widgets.get("_sidebar_preview_frame"), (
        "the picker on the rail is not the one belonging to the pane whose "
        "section is showing")


@pytest.mark.slow
def test_the_group_editor_survives_that_hand_over(loaded_app):
    """Handing the rail to the other pane must not be read as handing the
    editor over too: it is on the Statistics page and owes the rail nothing."""
    app = loaded_app
    ctx = _split_statistics_beside_segmentation(app)

    plate = ctx.widgets.get("_stats_map_plate")
    assert not plate.isHidden()
    assert plate not in _rail_plates(ctx)


@pytest.mark.slow
def test_a_single_statistics_pane_carries_its_own_editor(loaded_app):
    """Not a Split-View feature. Outside a split the editor is on the page for
    the same reason: the map that paints groups belongs with the cards it paints
    into, wherever the rail happens to be pointing."""
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    ctx = app._ctx

    plate = ctx.widgets.get_or_none("_stats_map_plate")
    assert plate is not None and not plate.isHidden()
    assert plate not in _rail_plates(ctx), (
        "the comparison-group map is mounted in the shared rail slot, where its "
        "visibility would again be a function of which pane has the focus")


@pytest.mark.slow
def test_the_rail_keeps_the_shared_picker_on_a_single_statistics_pane(loaded_app):
    """Outside a split, nothing is competing for the rail, so it goes on doing
    what it does on every other page.

    The editor moved off the rail because one slot cannot carry two verbs at
    once. With one pane there is no second claimant: the rail selects wells to
    plot, as everywhere else, and the page's own map paints groups. Leaving it
    empty was the alternative and read as an unfinished screen.
    """
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    ctx = app._ctx

    shown = [p for p in _rail_plates(ctx) if not p.isHidden()]
    assert len(shown) == 1, f"{len(shown)} pickers on the rail; expected one"
    assert shown[0] is ctx.widgets.get("_sidebar_plate"), (
        "the picker on the rail is not the shared plot selector")


@pytest.mark.slow
def test_it_gives_the_rail_up_again_the_moment_a_split_opens(loaded_app):
    """And the half that keeps the single-pane rule from undoing the fix.

    In a split the other pane has a claim on the rail, and a tab with no section
    taking it anyway is the ambiguity the editor was moved to remove. Asserts
    the picker on screen belongs to the *other* pane's section, not merely that
    there is one.
    """
    from well_viewer.core.rail_plate import section_of

    app = loaded_app
    ctx = _split_statistics_beside_segmentation(app)

    shown = [p for p in _rail_plates(ctx) if not p.isHidden()]
    assert len(shown) == 1, f"{len(shown)} pickers on the rail; expected one"
    assert section_of(shown[0]) is ctx.widgets.get("_sidebar_preview_frame"), (
        "Statistics kept the rail in a split, where the other pane has a claim "
        "on it")
