"""One well picker on the rail, and it belongs to the focused pane.

Four sidebar sections build their own ``WellPlateSelector`` — the shared one in
``_sidebar_main_frame``, plus Statistics, Segmentation and Image Table. They were
mutually exclusive for as long as only one section showed at a time. Showing
every visible pane's section put two of them on the rail at once: stacked,
identical-looking, and meaning different things — the Statistics plate paints
wells into comparison groups, the shared one selects wells to plot.

That regression got past its own test file because the test asserted the two
*frames* were visible and never asked what was inside them. Counting flags is not
counting pickers, so this file counts pickers.

The rule: sections follow visibility, plates follow focus — the same split the
heat-map drag mode already uses, since a plate is interaction.
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
    "_sidebar_stats_frame",
    "_sidebar_cell_browser_frame",
)


def _plates_in(frame):
    from widgets.well_plate_selector import WellPlateSelector
    return list(frame.findChildren(WellPlateSelector))


def _shown_plates(app):
    """Every well picker actually on the rail, with the frame that owns it."""
    out = []
    for name in _SECTION_FRAMES:
        frame = app._ctx.widgets.get_or_none(name)
        if frame is None or frame.isHidden():
            continue
        for plate in _plates_in(frame):
            if not plate.isHidden():
                out.append((name, plate))
    return out


def _frame_of(app, name):
    return app._ctx.widgets.get_or_none(name)


def _plate_shown(app, frame_name) -> bool:
    frame = _frame_of(app, frame_name)
    if frame is None or frame.isHidden():
        return False
    return any(not p.isHidden() for p in _plates_in(frame))


@pytest.mark.slow
def test_plots_beside_statistics_draws_one_picker(loaded_app):
    """The reported regression, in the arrangement it was reported in."""
    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")

    shown = _shown_plates(app)
    assert len(shown) == 1, (
        f"{len(shown)} well pickers on the rail; expected 1. "
        f"Frames showing one: {[s[0] for s in shown]}")


@pytest.mark.slow
def test_the_picker_that_survives_is_the_focused_pane_s(loaded_app):
    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")

    assert _plate_shown(app, "_sidebar_main_frame")
    assert not _plate_shown(app, "_sidebar_stats_frame")


@pytest.mark.slow
def test_focusing_statistics_hands_the_picker_over(loaded_app):
    """The other direction, and the reason the plate is hidden rather than the
    whole section: focus the Statistics pane and its group-painting plate is the
    one on the rail."""
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    _split(app, "Statistics", "Plotting")

    assert _plate_shown(app, "_sidebar_stats_frame")
    assert not _plate_shown(app, "_sidebar_main_frame")
    assert len(_shown_plates(app)) == 1


@pytest.mark.slow
def test_the_group_editor_still_shows_without_focusing_it(loaded_app):
    """What the plate suppression must not undo. The Statistics *section* is on
    screen unfocused — that is the whole point of the change this fixes — and
    only its duplicate picker is withheld."""
    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")

    stats_frame = _frame_of(app, "_sidebar_stats_frame")
    assert stats_frame is not None and not stats_frame.isHidden(), (
        "suppressing the duplicate picker must not take the group editor with it")
    grp = app._ctx.widgets.get_or_none("_stats_grp_inner")
    assert grp is not None and not grp.isHidden()


@pytest.mark.slow
def test_a_suppressed_picker_comes_back_on_the_next_tab_change(loaded_app):
    """Hiding is per-pass, not sticky. The plates are re-shown at the top of
    every tab change, so a section that regains the focus brings its picker with
    it rather than staying blank for the life of the session."""
    from well_viewer.core.split_view import exit_split

    app = loaded_app
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")
    assert not _plate_shown(app, "_sidebar_stats_frame")

    exit_split(app._ctx)
    app._notebook.setCurrentByName("Statistics")

    assert _plate_shown(app, "_sidebar_stats_frame"), (
        "the Statistics picker stayed suppressed after the split ended")
    assert len(_shown_plates(app)) == 1


@pytest.mark.slow
def test_a_single_pane_is_untouched(loaded_app):
    """No split, no suppression: one section, its own picker."""
    app = loaded_app
    app._notebook.setCurrentByName("Statistics")
    assert _plate_shown(app, "_sidebar_stats_frame")
    assert len(_shown_plates(app)) == 1

    app._notebook.setCurrentByName("Plotting")
    assert _plate_shown(app, "_sidebar_main_frame")
    assert len(_shown_plates(app)) == 1


def _frame_h(app, name):
    frame = app._ctx.widgets.get_or_none(name)
    return None if frame is None or frame.isHidden() else frame.height()


def _picker_height(app):
    for name in _SECTION_FRAMES:
        frame = app._ctx.widgets.get_or_none(name)
        if frame is None or frame.isHidden():
            continue
        for plate in _plates_in(frame):
            if not plate.isHidden():
                return plate.height()
    return None


@pytest.mark.slow
def test_a_section_without_a_picker_does_not_hold_the_rail_open(loaded_app):
    """Hiding a plate does not release its frame's share of the layout.

    Each section is added to the rail with a stretch of 1, so the frame whose
    plate had just been hidden went on claiming the same height and drew it as a
    hole. Measured on a 1600x1000 window: 303px of empty rail above the
    Statistics picker, which is what "a large blank space" was.
    """
    app = loaded_app
    app.resize(1600, 1000)
    app.show()          # geometry is only real once the layout has been shown
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")
    from well_viewer.core.split_view import focus_split
    focus_split(app._ctx, "Statistics")

    empty = _frame_h(app, "_sidebar_main_frame")
    assert empty is not None
    assert empty < 150, (
        f"the plate-less section is {empty}px tall — it is holding the rail "
        f"open around a picker it no longer has")


@pytest.mark.slow
def test_the_picker_keeps_its_size_whichever_pane_owns_it(loaded_app):
    """The void's other half: squeezed into what the empty frame left, the
    picker came out 152px tall against the 233px it has when it is the focused
    section's. A picker that changes size as you click between panes reads as
    two different controls."""
    from well_viewer.core.split_view import focus_split

    app = loaded_app
    app.resize(1600, 1000)
    app.show()          # geometry is only real once the layout has been shown
    app._notebook.setCurrentByName("Plotting")
    _split(app, "Plotting", "Statistics")

    focus_split(app._ctx, "Plotting")
    plots_h = _picker_height(app)
    focus_split(app._ctx, "Statistics")
    stats_h = _picker_height(app)

    assert plots_h and stats_h
    assert abs(plots_h - stats_h) <= 8, (
        f"the picker is {plots_h}px under one pane and {stats_h}px under the "
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
