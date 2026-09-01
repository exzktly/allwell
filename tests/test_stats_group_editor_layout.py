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


def _statistics_split(app):
    from PySide6.QtWidgets import QApplication
    from well_viewer.core.split_view import enter_split
    app.show()
    app._notebook.setCurrentByName("Statistics")
    app._notebook.setCurrentByName("Plotting")
    enter_split(app._ctx, "Plotting", "Statistics")
    QApplication.processEvents()


@pytest.mark.slow
def test_the_map_and_the_cards_never_overlap(loaded_app):
    """The defect itself, swept across the sizes a user can produce."""
    from PySide6.QtWidgets import QApplication

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)

    plate = ctx.widgets.get("_stats_map_plate")
    side = ctx.widgets.get("_stats_group_side")

    for w, h in _SIZES:
        app.resize(w, h)
        QApplication.processEvents()
        QApplication.processEvents()
        a, b = _rect(plate), _rect(side)
        assert not a.intersects(b), (
            f"at {w}x{h} the group cards {b} are drawn over the well map {a}")


@pytest.mark.slow
def test_it_is_side_by_side_when_there_is_room_and_a_column_when_there_is_not(loaded_app):
    """Not "it does not overlap", which a row that always stacked would also
    satisfy — and stacking a wide pane would cost the results half the page."""
    from PySide6.QtWidgets import QApplication

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    row = ctx.widgets.get("_stats_group_row")

    QApplication.processEvents()
    assert not row.is_stacked(), (
        "a 1600x1000 window split in two has room for the map beside the cards")

    app.resize(1100, 800)
    QApplication.processEvents()
    assert row.is_stacked(), (
        "a 350px pane cannot hold both panels at their minimums, so the row has "
        "to become a column rather than clip")


@pytest.mark.slow
def test_the_map_keeps_its_floor_rather_than_being_squeezed(loaded_app):
    """The cards are text and they scroll; the map is not and does not. So the
    map holds ``PLATE_MIN_WIDTH`` and the cards give."""
    from PySide6.QtWidgets import QApplication
    from widgets.plate_geometry import PLATE_MIN_WIDTH

    app = loaded_app
    ctx = app._ctx
    app.resize(1600, 1000)
    _statistics_split(app)
    plate = ctx.widgets.get("_stats_map_plate")

    for w, h in _SIZES:
        app.resize(w, h)
        QApplication.processEvents()
        QApplication.processEvents()
        assert plate.width() >= PLATE_MIN_WIDTH, (
            f"at {w}x{h} the map is {plate.width()}px wide, under its "
            f"{PLATE_MIN_WIDTH}px floor")
