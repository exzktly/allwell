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
    """Nothing to measure, so nothing to decide — and no division by a panel
    count of zero in ``needed_width``."""
    from widgets.responsive_row import ResponsiveRow
    row = ResponsiveRow()
    assert row.needed_width() == 0
    row.apply_orientation(avail=0)
    assert not row.is_stacked()
