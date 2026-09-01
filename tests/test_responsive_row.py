"""A row of panels becomes a column before it starts clipping them.

A ``QHBoxLayout`` handed less width than its children's minimums does not
decline to lay them out — it overlaps and clips. That is what the Statistics
comparison-group editor did on a 350px pane: the group cards drawn over the
well map, a section title reading "COMP GROU", a button cut off mid-word.

``widgets/responsive_row.ResponsiveRow`` flips to a column on the panels' own
``minimumSizeHint`` widths, so the flip point is not a number anybody has to
keep in step with the panels' contents. These tests are the fast lane's half of
that: pure widgets, no app. ``tests/test_stats_group_editor_layout.py`` is the
other half, and asserts that the real editor's panels do not overlap.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def row_with_two_panels(qapp):
    """A row whose two panels need 100 + 8 + 200 = 308px side by side."""
    from PySide6.QtWidgets import QWidget
    from widgets.responsive_row import ResponsiveRow

    row = ResponsiveRow(spacing=8)
    left, right = QWidget(), QWidget()
    left.setMinimumWidth(100)
    right.setMinimumWidth(200)
    row.add_panel(left, stretch=0)
    row.add_panel(right, stretch=1)
    return row, left, right


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_flip_point_is_the_panels_own_minimums(row_with_two_panels):
    """Not a chosen number. 100 + 200 with 8px of spacing between them."""
    row, _left, _right = row_with_two_panels
    assert row.needed_width() == 308


def test_a_wide_row_stays_on_one_line(row_with_two_panels):
    row, _left, _right = row_with_two_panels
    row.apply_orientation(avail=800)
    assert not row.is_stacked()


def test_it_stacks_one_pixel_before_it_would_clip(row_with_two_panels):
    """At exactly the needed width it still fits; one less and it does not.

    Asserting the boundary rather than "it stacks when narrow": a rule that
    stacks at some unspecified narrower width is satisfied by any threshold at
    all, including one that stacks a row with room to spare.
    """
    row, _left, _right = row_with_two_panels
    row.apply_orientation(avail=308)
    assert not row.is_stacked()
    row.apply_orientation(avail=307)
    assert row.is_stacked()


def test_it_does_not_unstack_the_moment_it_could(row_with_two_panels):
    """Hysteresis. The two orientations have different heights and so different
    scrollbar states, and a width sitting on the flip point would otherwise
    oscillate between them."""
    row, _left, _right = row_with_two_panels
    row.apply_orientation(avail=200)
    assert row.is_stacked()
    row.apply_orientation(avail=320)          # past the need, inside the margin
    assert row.is_stacked()
    row.apply_orientation(avail=400)          # clear of it
    assert not row.is_stacked()


def test_stacking_drops_the_horizontal_stretch(row_with_two_panels):
    """A panel that grabbed the horizontal slack would grab the vertical too
    once the row is a column, and the page below it would lose the room the row
    was meant to leave."""
    row, _left, right = row_with_two_panels
    layout = right.parentWidget().layout()
    row.apply_orientation(avail=800)
    assert layout.stretch(1) == 1
    row.apply_orientation(avail=100)
    assert layout.stretch(1) == 0
    row.apply_orientation(avail=800)
    assert layout.stretch(1) == 1, "the stretch did not come back"


def test_each_panel_takes_the_alignment_for_the_orientation(qapp):
    """Top-aligned beside its neighbour, centred above it — one widget, two
    placements, chosen by which way the row is running."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget
    from widgets.responsive_row import ResponsiveRow

    row = ResponsiveRow(spacing=0)
    panel = QWidget()
    panel.setMinimumWidth(400)
    row.add_panel(panel, row_alignment=Qt.AlignTop,
                  column_alignment=Qt.AlignHCenter)
    other = QWidget()
    other.setMinimumWidth(400)
    row.add_panel(other)

    row.apply_orientation(avail=1000)
    assert row.layout().itemAt(0).alignment() == Qt.AlignTop
    row.apply_orientation(avail=100)
    assert row.layout().itemAt(0).alignment() == Qt.AlignHCenter


def test_an_empty_row_is_not_a_flip(qapp):
    """Nothing to measure, so nothing to decide.

    ``spacing=8``, and that is the whole test. With the default ``spacing=0``
    the unguarded arithmetic — ``0 + spacing * (0 - 1)`` — also comes out 0, so
    deleting both empty-row guards left this file green and the fixture was
    proving the guards were there by agreeing with their absence.
    """
    from widgets.responsive_row import ResponsiveRow
    row = ResponsiveRow(spacing=8)
    assert row.needed_width() == 0, "one negative gap for the panels not there"
    row.apply_orientation(avail=0)
    assert not row.is_stacked()


def test_a_hidden_panel_is_not_charged_for_width(row_with_two_panels):
    """A layout reserves nothing for a widget it is not drawing, so neither may
    the measurement — counting a hidden panel stacks a row with room to spare,
    and a host that shows and hides one is the caller most likely to be sitting
    near the boundary."""
    row, _left, right = row_with_two_panels
    right.hide()
    assert row.needed_width() == 100, (
        "the hidden panel is still being charged for, and so is the gap that "
        "would have been beside it")
    row.apply_orientation(avail=150)
    assert not row.is_stacked(), (
        "a 100px panel stacked in 150px of room because of a panel that is not "
        "on screen")


def test_adding_a_panel_re_decides_the_orientation(qapp):
    """A row already sized and then handed another panel needs more width than
    the decision on file was made with. Nothing else asks again until the next
    resize, which may never come."""
    from PySide6.QtWidgets import QWidget
    from widgets.responsive_row import ResponsiveRow

    row = ResponsiveRow(spacing=8)
    first = QWidget()
    first.setMinimumWidth(200)
    row.add_panel(first)
    row.resize(300, 100)
    row.apply_orientation()
    assert not row.is_stacked()

    second = QWidget()
    second.setMinimumWidth(200)
    row.add_panel(second)
    assert row.is_stacked(), (
        "the second panel arrived on a row that had already decided it fits, "
        "and the row went on clipping until something else resized it")


def test_a_row_reads_the_width_of_the_scroll_area_holding_it(qapp):
    """The case the row's own width cannot report.

    A scroll area with horizontal scrolling off sizes its inner widget to at
    least that widget's minimum, so once the row is too wide the whole page is
    too wide and every width up the chain repeats the same inflated number. The
    viewport is the one honest measurement, and without it the flip that would
    relieve the overflow never fires.
    """
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from widgets.responsive_row import ResponsiveRow, ResponsiveScroll

    scroll = ResponsiveScroll()
    host = QWidget()
    QVBoxLayout(host).setContentsMargins(0, 0, 0, 0)
    row = ResponsiveRow(spacing=8)
    for _ in range(2):
        panel = QWidget()
        panel.setMinimumWidth(300)
        row.add_panel(panel)
    host.layout().addWidget(row)
    scroll.setWidget(host)
    scroll.resize(400, 300)
    scroll.show()

    assert row.width() >= row.needed_width(), (
        "the row is not pinned wider than its viewport, so this test is not "
        "measuring the case it was written for")
    scroll.refresh_rows()
    assert row.is_stacked(), (
        f"the row is {row.width()}px inside a {scroll.viewport().width()}px "
        f"viewport and did not stack — it read its own inflated width")

