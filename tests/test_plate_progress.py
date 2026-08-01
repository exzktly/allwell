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
from widgets.plate_progress import PlateProgress  # noqa: E402


class _NoPlateWithLegend(PlateProgress):
    """Minimal concrete subclass exercising show_plate=False with the legend
    column left on (show_legend's default) -- no production dashboard uses
    this exact combination today (Subset, the only show_plate=False
    consumer, also turns the legend off), but the branch is still real,
    public, shared-infra code, so it gets its own direct coverage rather than
    riding on a subclass that no longer takes this path."""

    def __init__(self, parent=None) -> None:
        super().__init__("No Plate", parent, show_plate=False)

    def _build_rail(self) -> None:
        pass

    def _build_legend(self) -> None:
        pass

    def _well_tooltip(self, well_id: str) -> str:
        return ""


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
    d = _NoPlateWithLegend()
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


def test_show_legend_false_drops_the_legend_column_and_its_divider(qapp):
    """Regression guard (UX audit P1-8): Subset's status strip used to carry
    a rail column AND a legend column that said the same four things twice
    -- a coloured dot + state label in both, the rail's just adding a live
    count. show_legend=False must remove the column and its divider
    entirely, not just hide it, the same way show_plate=False removes the
    centre column rather than leaving a blank one."""
    d = SubsetDashboard()
    assert not hasattr(d, "_legend_host")
    assert not hasattr(d, "_legend_lay")
    vrules = [c for c in d._body.findChildren(QFrame) if c.objectName() == "VRule"]
    assert vrules == []

    d.resize(1300, 260)
    d.show()
    qapp.processEvents()
    qapp.processEvents()
    # The rail keeps its fixed width regardless of how much extra room the
    # window has -- the trailing stretch (not the rail) absorbs the leftover,
    # so the single remaining column doesn't stretch to fill the page.
    assert d._rail_host.width() == 238


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


def test_the_rail_picker_is_exempt_from_the_size_caps(qapp):
    """Review's left-rail well picker keeps its own size.

    Unifying every plate to one geometry initially capped the rail picker at the
    dashboards' budget too, which shrank its cells from 23.7px to 20.8px — it
    unified the two by making the one people actually click on smaller. The rail
    is the app's primary interaction surface, not a status readout: it scales to
    whatever width the rail gives it, with no ceiling. It still shares the size
    *policy*, because that part genuinely is the same rule; only the cap differs.
    """
    from widgets.plate_geometry import (
        PLATE_MAX_HEIGHT,
        PLATE_MAX_WIDTH,
        apply_plate_geometry,
    )
    from widgets.well_plate_selector import WellPlateSelector

    rail = WellPlateSelector()
    apply_plate_geometry(rail, role="rail")
    assert rail.maximumWidth() > PLATE_MAX_WIDTH
    assert rail.maximumHeight() > PLATE_MAX_HEIGHT

    status = WellPlateSelector()
    apply_plate_geometry(status, role="status")
    assert status.maximumWidth() == PLATE_MAX_WIDTH
    assert status.maximumHeight() == PLATE_MAX_HEIGHT

    assert rail.sizePolicy().hasHeightForWidth()
    assert status.sizePolicy().hasHeightForWidth()
