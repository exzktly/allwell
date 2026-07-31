"""Bare-`QApplication` widget tests for `widgets.plate_progress.PlateProgress`,
covering the shared chrome every Datasets/Analyze dashboard subclasses.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFrame  # noqa: E402

from datasets.concatenate.dashboard import ConcatDashboard  # noqa: E402
from datasets.subset.dashboard import SubsetDashboard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_headline_word_wraps(qapp):
    """Regression guard: PlateProgress._headline had no setWordWrap(True) —
    a real failure message long enough to name every colliding well token
    forced a horizontal scrollbar on the whole page and pushed the rest of
    the page's content off-screen instead of wrapping onto a second line."""
    d = ConcatDashboard()
    assert d._headline.wordWrap() is True


def test_legend_host_wide_enough_for_the_longest_current_label(qapp):
    """Regression guard: a fixed 140px legend column clipped
    "merged — no CSV on one side" to "merged — no CSV", and clipped Merge
    Plates' two differently-coloured "merged — dataset N" entries to the
    identical "merged — dataset" — the one thing the legend exists to say."""
    d = ConcatDashboard()
    assert d._legend_host.width() >= 180


def test_no_dead_gap_between_rail_and_legend_when_plate_is_skipped(qapp):
    """Regression guard: with show_plate=False, every remaining item
    (rail, one divider, legend) was fixed-width with no widget left in the
    body layout carrying a stretch factor -- Qt distributed the page's
    leftover width as blank padding around those fixed items instead,
    reading as a ~190px dead void between the divider and the legend.
    A trailing stretch must collect that leftover at the row's own right
    edge so rail/divider/legend stay packed together at the left."""
    d = SubsetDashboard()
    d.resize(1300, 260)
    d.show()
    qapp.processEvents()
    qapp.processEvents()

    rail_right = d._rail_host.x() + d._rail_host.width()
    legend_left = d._legend_host.x()
    gap = legend_left - rail_right
    # One QFrame divider plus two lots of the body layout's own spacing (14px
    # each side of the divider) is the only content that legitimately belongs
    # in this gap -- generous headroom without re-admitting the ~190px void.
    assert gap < 60, f"rail-to-legend gap is {gap}px -- looks like the dead-gap bug is back"

    vrules = [c for c in d._body.findChildren(QFrame) if c.objectName() == "VRule"]
    assert len(vrules) == 1, "show_plate=False should carry exactly one divider, not two"


def test_show_plate_true_still_has_two_dividers_and_a_stretchy_centre(qapp):
    """The show_plate=False fix must not regress the normal (plate-bearing)
    layout: two dividers flank the centre column, which still owns the
    stretch factor that absorbs the page's leftover width."""
    d = ConcatDashboard()
    d.resize(1300, 260)
    d.show()
    qapp.processEvents()
    qapp.processEvents()

    vrules = [c for c in d._body.findChildren(QFrame) if c.objectName() == "VRule"]
    assert len(vrules) == 2

    center_right = d._center_host.x() + d._center_host.width()
    legend_left = d._legend_host.x()
    assert legend_left - center_right < 60
