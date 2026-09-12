"""One well picker on the rail, it belongs to the focused pane, and it does not
move.

Three sidebar sections offer a ``WellPlateSelector`` — the shared one for
``_sidebar_main_frame``, plus Segmentation and Image Table. They were mutually
exclusive for as long as only one section showed at a time. Showing every
visible pane's section put two of them on the rail at once: stacked and
identical-looking while selecting for different pages.

The pair used here is Plots beside Image Browser, parked on Segmentation. It was
Plots beside Statistics, whose plate did not merely select for a different page
but meant something else entirely — a click on it painted a well into a named
comparison group. That plate is no longer on the rail at all: the comparison-group
editor is part of the Statistics page (``tests/test_stats_groups_need_no_focus.py``),
so every picker the rail still arbitrates between answers the same question,
"which wells am I looking at". The arbitration rule below is unchanged and still
needs a test.

The original regression got past its own test file because the test asserted the
two *frames* were visible and never asked what was inside them. Counting flags is
not counting pickers, so this file counts pickers.

The rule: sections follow visibility, plates follow focus — the same split the
heat-map drag mode already uses, since a plate is interaction.

Choosing whose picker shows must not be a change of anyone's *height*, which is
what it was while each section built its plate into itself. The unfocused
section then collapsed by the height of the picker it was no longer showing, and
everything below it slid up: the plot-groups drawer moved 333px between the two
focus states, and reserving the missing picker's space instead put back a
picker-shaped hole. All three plates live in one slot above the sections now, so
neither happens — see ``review/core/rail_plate.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")


def _split(app, tab_a, tab_b):
    from review.core.split_view import enter_split
    enter_split(app._ctx, tab_a, tab_b)
    return app._visible_centre_tabs()


def _park_on_segmentation(app):
    """Segmentation is a leaf inside the Image Browser container, and it is the
    leaf that owns ``_sidebar_preview_frame`` and its picker."""
    from review.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app._ctx, "Segmentation")


#: The per-tab sidebar sections, named here rather than imported from
#: ``tab_controller``. The fix under test adds helpers there, and a test that
#: reached for them would fail on the pre-fix tree with an ``ImportError`` —
#: which looks like the guard biting and measures nothing at all. Counting the
#: pickers has to work identically on both sides of the fix.
_SECTION_FRAMES = (
    "_sidebar_main_frame",
    "_sidebar_preview_frame",
    "_sidebar_image_table_frame",
    "_sidebar_sample_frame",
    "_sidebar_cell_browser_frame",
)


def _plates_in(app, frame):
    """The pickers that serve *frame*, wherever they are mounted.

    Reached through the registry key and the plate's own recorded section
    rather than by importing the slot module, so a build that never assembled
    the shell — or a future one that mounts plates somewhere else again — still
    reports what is on the rail instead of raising.
    """
    from widgets.well_plate_selector import WellPlateSelector
    slot = app._ctx.widgets.get_or_none("_sidebar_plate_slot")
    out = []
    if slot is not None:
        out += [p for p in slot.findChildren(WellPlateSelector)
                if getattr(p, "_aw_rail_section", None) is frame]
    return out + list(frame.findChildren(WellPlateSelector))


def _shown_plates(app):
    """Every well picker actually on the rail, with the frame that owns it."""
    out = []
    for name in _SECTION_FRAMES:
        frame = app._ctx.widgets.get_or_none(name)
        if frame is None or frame.isHidden():
            continue
        for plate in _plates_in(app, frame):
            if not plate.isHidden():
                out.append((name, plate))
    return out


def _frame_of(app, name):
    return app._ctx.widgets.get_or_none(name)


def _plate_shown(app, frame_name) -> bool:
    frame = _frame_of(app, frame_name)
    if frame is None or frame.isHidden():
        return False
    return any(not p.isHidden() for p in _plates_in(app, frame))


@pytest.mark.slow
def test_two_plate_bearing_panes_draw_one_picker(loaded_app):
    """The reported regression: two sections with a plate each, one rail."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")

    shown = _shown_plates(app)
    assert len(shown) == 1, (
        f"{len(shown)} well pickers on the rail; expected 1. "
        f"Frames showing one: {[s[0] for s in shown]}")


@pytest.mark.slow
def test_the_picker_that_survives_is_the_focused_pane_s(loaded_app):
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")

    assert _plate_shown(app, "_sidebar_main_frame")
    assert not _plate_shown(app, "_sidebar_preview_frame")


@pytest.mark.slow
def test_focusing_the_other_pane_hands_the_picker_over(loaded_app):
    """The other direction, and the reason the plate is hidden rather than the
    whole section: focus the Image Browser pane and its picker is the one on the
    rail."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Image Browser")
    _split(app, "Image Browser", "Plotting")

    assert _plate_shown(app, "_sidebar_preview_frame")
    assert not _plate_shown(app, "_sidebar_main_frame")
    assert len(_shown_plates(app)) == 1


@pytest.mark.slow
def test_the_unfocused_section_still_shows_without_focusing_it(loaded_app):
    """What the plate suppression must not undo. The unfocused pane's *section*
    is on screen — that is the whole point of the change this fixes — and only
    its duplicate picker is withheld."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")

    frame = _frame_of(app, "_sidebar_preview_frame")
    assert frame is not None and not frame.isHidden(), (
        "suppressing the duplicate picker must not take its section with it")
    lbl = app._ctx.widgets.get_or_none("_preview_sel_lbl")
    assert lbl is not None and not lbl.isHidden()


@pytest.mark.slow
def test_a_suppressed_picker_comes_back_on_the_next_tab_change(loaded_app):
    """Hiding is per-pass, not sticky. Every tab change speaks for every plate
    — the ones whose section is off screen included, since a plate in the shared
    slot is not hidden by its section being hidden — so a section that regains
    the focus brings its picker with it rather than staying blank for the life
    of the session."""
    from review.core.split_view import exit_split

    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")
    assert not _plate_shown(app, "_sidebar_preview_frame")

    exit_split(app._ctx)
    app._notebook.setCurrentByName("Image Browser")

    assert _plate_shown(app, "_sidebar_preview_frame"), (
        "the Segmentation picker stayed suppressed after the split ended")
    assert len(_shown_plates(app)) == 1


@pytest.mark.slow
def test_a_single_pane_is_untouched(loaded_app):
    """No split, no suppression: one section, its own picker."""
    app = loaded_app
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Image Browser")
    assert _plate_shown(app, "_sidebar_preview_frame")
    assert len(_shown_plates(app)) == 1

    app._notebook.setCurrentByName("Plotting")
    assert _plate_shown(app, "_sidebar_main_frame")
    assert len(_shown_plates(app)) == 1


def _picker_height(app):
    shown = _shown_plates(app)
    return shown[0][1].height() if shown else None


def _rail_geometry(app):
    """Where every visible widget on the rail sits, as a comparable mapping.

    Keyed on the path from the rail root plus the class name rather than on
    ``objectName``, because most of these widgets have none and a key that
    collapses them all to "" compares nothing. The picker is excluded: which
    *object* is on the rail is the one thing focus is meant to change, and it is
    asserted on its own below.
    """
    from widgets.well_plate_selector import WellPlateSelector
    out = {}

    def walk(w, path):
        for i, child in enumerate(w.children()):
            if not hasattr(child, "isHidden") or child.isHidden():
                continue
            if isinstance(child, WellPlateSelector):
                continue
            key = f"{path}/{i}:{type(child).__name__}"
            g = child.mapToGlobal(child.rect().topLeft())
            out[key] = (g.x(), g.y(), child.width(), child.height())
            walk(child, key)

    walk(app._sidebar_root, "")
    return out


def _split_and_show(app):
    app.resize(1600, 1000)
    app.show()          # geometry is only real once the layout has been shown
    _park_on_segmentation(app)
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Image Browser")


@pytest.mark.slow
def test_nothing_on_the_rail_moves_when_the_focus_does(loaded_app):
    """The reported symptom, at the level of the whole rail.

    Each section used to build its own picker, so the unfocused one collapsed by
    the picker's height and carried everything below it up the rail: measured on
    a 1600x1000 window, the plot-groups drawer sat at y=789 under Plotting and
    y=456 under the other. Reserving the space instead is the same bug wearing
    the other face — a 303px hole where the missing picker would be. With one
    slot above the sections, choosing whose picker shows changes no one's
    height, so this compares the entire rail rather than any one widget.
    """
    from review.core.split_view import focus_split

    app = loaded_app
    _split_and_show(app)

    focus_split(app._ctx, "Plotting")
    plots = _rail_geometry(app)
    focus_split(app._ctx, "Image Browser")
    browser = _rail_geometry(app)

    moved = {k: (plots[k], browser[k]) for k in plots.keys() & browser.keys()
             if plots[k] != browser[k]}
    assert not moved, (
        "the rail moves when the focus does: "
        + "; ".join(f"{k} {a} -> {b}" for k, (a, b) in sorted(moved.items())[:6]))


@pytest.mark.slow
def test_the_rail_returns_to_where_it_started(loaded_app):
    """Going back to the pane you came from is not a third layout.

    Hiding a picker only posts the layout invalidation that reclaims its space,
    and the rail was laid out again before that event arrived — so the slot went
    on reserving room for a picker it had just hidden. Measured, the round trip
    left the slot 336px tall with the picker squeezed to 168 against the 233 it
    has in every other state.
    """
    from review.core.split_view import focus_split

    app = loaded_app
    _split_and_show(app)

    focus_split(app._ctx, "Plotting")
    before = _rail_geometry(app)
    focus_split(app._ctx, "Image Browser")
    focus_split(app._ctx, "Plotting")
    after = _rail_geometry(app)

    drifted = {k: (before[k], after[k]) for k in before.keys() & after.keys()
               if before[k] != after[k]}
    assert not drifted, (
        "the rail did not come back: "
        + "; ".join(f"{k} {a} -> {b}" for k, (a, b) in sorted(drifted.items())[:6]))


@pytest.mark.slow
def test_the_picker_keeps_its_size_whichever_pane_owns_it(loaded_app):
    """A picker that changes size as you click between panes reads as two
    different controls. It was 152px under one pane and 233px under the other,
    squeezed into what the plate-less section left it."""
    from review.core.split_view import focus_split

    app = loaded_app
    _split_and_show(app)

    focus_split(app._ctx, "Plotting")
    plots_h = _picker_height(app)
    focus_split(app._ctx, "Image Browser")
    browser_h = _picker_height(app)

    assert plots_h and browser_h
    assert plots_h == browser_h, (
        f"the picker is {plots_h}px under one pane and {browser_h}px under the "
        f"other")


@pytest.mark.slow
def test_two_plot_panes_share_one_picker(loaded_app):
    """Both panes' sections are the same frame here, so there is one plate and
    nothing to choose between — the rule must not hide it for lack of a
    'newly shown' frame to keep."""
    app = loaded_app
    assert app._plotting_notebook.setCurrentByName("Bar Plots")
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Spatial")

    assert _plate_shown(app, "_sidebar_main_frame")
    assert len(_shown_plates(app)) == 1
