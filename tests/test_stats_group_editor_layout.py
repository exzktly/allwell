"""The comparison-group editor's two panels never draw on top of each other.

The map and the group cards sit side by side on the Statistics page. A
``QHBoxLayout`` handed less width than its children's minimums does not decline
to lay them out — it overlaps and clips — and that is what shipped in the first
cut of this editor: measured on an 1100x800 window with the rail open and the
pane split (about 350px per pane), the cards were drawn over the map, the
section title read "COMP GROU" and the load button was cut off mid-word.

``widgets/responsive_row`` stacks the two before that happens, on the panels'
own effective minimum widths. ``tests/test_responsive_row.py`` pins that rule on
bare widgets; this file pins the consequence on the real page, because the rule
is only worth what the real panels' minimums make it — and the first version of
it read ``minimumSizeHint`` alone, which ignores the map's explicit floor and
would have gone on clipping in exactly the place it was written to fix.

Slow lane: the widths that matter are the ones the real shell produces.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")

#: Window sizes to sweep, from comfortable down to the one that broke.
_SIZES = ((1600, 1000), (1400, 900), (1200, 800), (1100, 800), (1000, 700))


def _rect(widget):
    """*widget*'s box in window coordinates, so two siblings are comparable."""
    from PySide6.QtCore import QRect
    top_left = widget.mapTo(widget.window(), widget.rect().topLeft())
    return QRect(top_left, widget.size())


def _settle(ms: int = 0) -> None:
    """Let the layout finish.

    A single ``processEvents`` catches a transient mid-resize layout — measured
    once as a map overflowing its pane by 60px that the settled geometry did not
    have. An assertion against that state would fail on a layout no user ever
    sees, which is the same defect as passing on one.
    """
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    for _ in range(6):
        QApplication.processEvents()
    if ms:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        for _ in range(6):
            QApplication.processEvents()


def _statistics_split(app):
    from well_viewer.core.split_view import enter_split
    app.show()
    app._notebook.setCurrentByName("Statistics")
    app._notebook.setCurrentByName("Plotting")
    enter_split(app._ctx, "Plotting", "Statistics")
    _settle()


def _two_groups(ctx):
    """Two named groups with wells in them — the state the tab needs to run."""
    from well_viewer.features.stats.controller import (
        stats_grp_add, stats_refresh_group_list,
    )
    wells = list(ctx.state.dataset.well_paths)
    ctx.state.stats.clear_groups()
    for name, chunk in (("Control", wells[:3]), ("Treated", wells[3:6])):
        group = stats_grp_add(ctx) or ctx.state.stats.active_group()
        group.name = name
        group.solo_wells.extend(chunk)
    stats_refresh_group_list(ctx)


@pytest.mark.slow
def test_the_map_and_the_cards_never_overlap(loaded_app):
    """The defect itself, swept across the sizes a user can produce."""

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)

    plate = ctx.widgets.get("_stats_map_plate")
    side = ctx.widgets.get("_stats_group_side")

    for w, h in _SIZES:
        app.resize(w, h)
        _settle()
        a, b = _rect(plate), _rect(side)
        assert not a.intersects(b), (
            f"at {w}x{h} the group cards {b} are drawn over the well map {a}")


@pytest.mark.slow
def test_it_is_side_by_side_when_there_is_room_and_a_column_when_there_is_not(loaded_app):
    """Not "it does not overlap", which a row that always stacked would also
    satisfy — and stacking a wide pane would cost the results half the page."""

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    row = ctx.widgets.get("_stats_group_row")

    _settle()
    assert not row.is_stacked(), (
        "a 1600x1000 window split in two has room for the map beside the cards")

    app.resize(1100, 800)
    _settle()
    assert row.is_stacked(), (
        "a 350px pane cannot hold both panels at their minimums, so the row has "
        "to become a column rather than clip")


@pytest.mark.slow
def test_the_map_keeps_its_floor_rather_than_being_squeezed(loaded_app):
    """The cards are text and they scroll; the map is not and does not. So the
    map holds ``PLATE_MIN_WIDTH`` and the cards give."""
    from widgets.plate_geometry import PLATE_MIN_WIDTH

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    plate = ctx.widgets.get("_stats_map_plate")

    for w, h in _SIZES:
        app.resize(w, h)
        _settle()
        assert plate.width() >= PLATE_MIN_WIDTH, (
            f"at {w}x{h} the map is {plate.width()}px wide, under its "
            f"{PLATE_MIN_WIDTH}px floor")


@pytest.mark.slow
def test_a_well_never_shrinks_below_what_it_can_be_dragged_at(loaded_app):
    """The width floor is only half the claim.

    The grid sizes its cells to ``min(width / 12, height / 8)``, so a map held
    at 220px wide and squeezed vertically has cells just as small — measured,
    before the page scrolled, a 330x72 map: nine pixels of row and under six of
    well, for a control whose entire purpose is a drag gesture.

    So the assertion is on the **cell**, in both directions, against the size
    ``PLATE_MIN_WIDTH`` states is the smallest a well may be. Nothing applies
    that as a vertical minimum on the widget — a ``setMinimumHeight`` beside it
    was measured to change no geometry at any of these sizes and was removed —
    which is exactly why the outcome is what gets pinned rather than the
    mechanism: whatever stops compressing the map, this notices.
    """
    from widgets.plate_geometry import PLATE_MIN_WIDTH

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    _two_groups(ctx)
    plate = ctx.widgets.get("_stats_map_plate")
    smallest_well = PLATE_MIN_WIDTH / 12

    for w, h in _SIZES:
        app.resize(w, h)
        _settle()
        cell = min(plate.width() / 12, plate.height() / 8)
        assert cell >= smallest_well, (
            f"at {w}x{h} the map is {plate.width()}x{plate.height()}, which is "
            f"{cell:.1f}px of well against the {smallest_well:.1f}px "
            f"PLATE_MIN_WIDTH allows")


@pytest.mark.slow
def test_two_group_cards_fit_before_the_list_scrolls(loaded_app):
    """The tab needs two groups and its own empty state asks for two, so a list
    that can show one is the software disagreeing with itself.

    Asserts the list is tall enough for the two cards *it built this pass*,
    not a fixed number of pixels: a card is a dot, a name, a muted count and two
    buttons at whatever the current font and DPI make of them.
    """
    from PySide6.QtWidgets import QFrame

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    _two_groups(ctx)
    inner = ctx.widgets.get("_stats_grp_inner")
    cards = [w for w in inner.findChildren(QFrame)
             if w.objectName() == "StatsGroupCard"]
    assert len(cards) == 2, f"expected two group cards, drew {len(cards)}"
    needed = sum(c.sizeHint().height() for c in cards)
    canvas = ctx.widgets.get("_stats_grp_canvas")

    for w, h in _SIZES:
        app.resize(w, h)
        _settle()
        assert canvas.height() >= needed, (
            f"at {w}x{h} the group list is {canvas.height()}px for two cards "
            f"needing {needed}px, so one of them is behind a scrollbar while "
            f"the panel below asks for two groups")


@pytest.mark.slow
def test_the_page_scrolls_rather_than_crushing_the_editor(loaded_app):
    """Where the shortfall goes when the floors do not fit.

    Something has to give on a short pane, and the honest answer is that the
    page is longer than the window — not that the map is nine pixels of row.
    Asserts the scrollbar actually has range, not merely that a scroll area
    exists: an area whose content always fits is one this test could not tell
    from no scroll area at all.
    """
    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    _two_groups(ctx)
    scroll = ctx.widgets.get("_stats_page_scroll")

    app.resize(1000, 700)
    _settle()
    assert scroll.verticalScrollBar().maximum() > 0, (
        "the page fits a 1000x700 split pane, so this test is measuring "
        "nothing — pick a size where the floors do not fit")
    assert scroll.horizontalScrollBar().maximum() == 0, (
        "the page scrolls sideways, so width no longer forces the editor to "
        "stack and it can go on clipping instead")


@pytest.mark.slow
def test_folding_the_editor_hands_its_height_to_the_results(loaded_app):
    """What the collapse is for. Asserts the results panel actually grows by
    about what the editor gave up — "the section got shorter" is satisfied by a
    fold that leaves the space empty."""
    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    _two_groups(ctx)
    section = ctx.widgets.get("_stats_group_section")
    results = ctx.widgets.get("_stats_res_frame")

    _settle()
    strip_open, results_open = section.height(), results.height()
    assert section.isExpanded()

    section.setExpanded(False)
    _settle(400)
    strip_shut, results_shut = section.height(), results.height()
    assert strip_shut < strip_open, "the editor did not fold"
    gained = results_shut - results_open
    given = strip_open - strip_shut
    assert gained >= given * 0.8, (
        f"the editor gave up {given}px and the results gained {gained}px — the "
        f"space went somewhere other than the panel below it")

    section.setExpanded(True)
    _settle(400)
    assert section.height() == strip_open, (
        f"re-opening left the editor {section.height()}px against the "
        f"{strip_open}px it had")
