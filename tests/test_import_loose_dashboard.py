"""Bare-`QApplication` widget tests for
`datasets.import_loose.dashboard.ImportLooseDashboard`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from datasets.import_loose.dashboard import ImportLooseDashboard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_wells_and_stage_updates_overall(qapp):
    d = ImportLooseDashboard()
    d.set_wells(["A01", "B02", "c3"])
    assert d._wells == ["A01", "B02", "C03"]
    assert d.overall_maximum() == 3
    d.set_well_stage("A01", "writing")
    assert d._bar.value() == 0
    d.set_well_stage("A01", "done")
    d.set_well_stage("B02", "done")
    assert d._bar.value() == 2


def test_reset_clears_state(qapp):
    d = ImportLooseDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "done")
    d.reset()
    assert d._wells == []
    assert d._bar.value() == 0
    assert d._headline.text() == "Idle"


def test_rail_and_legend_use_the_same_word_for_the_done_state(qapp):
    """Regression guard (UX audit P1-8): the rail counted the "done" state as
    "Done: N" while the legend and the well tooltip both called the same state
    something else -- two different words for one state, right next to each
    other in the same status strip.

    Asserted as an agreement between the two rather than against a literal, so
    the guard survives the tool's vocabulary changing. It pinned "Converted"
    when this page still described itself as converting; the page now says zip
    throughout, and a test of "do these two agree" should not have needed
    editing for that.
    """
    d = ImportLooseDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "done")

    rail_text = " ".join(lbl.text() for lbl in d._rail_host.findChildren(QLabel))
    legend_text = " ".join(lbl.text() for lbl in d._legend_host.findChildren(QLabel))
    assert "Done" not in rail_text

    # Every state the rail counts must be the word the legend uses for it --
    # checked for all of them, not just the done one, since the mismatch this
    # guards against can appear on any state.
    import re

    rail_words = {m.group(1) for m in re.finditer(r"(\w+):\s*\d+", rail_text)}
    assert rail_words, f"no counted states found in the rail: {rail_text!r}"
    missing = [w for w in rail_words if w.lower() not in legend_text.lower()]
    assert not missing, (
        f"the rail counts {missing} but the legend calls them something else "
        f"— rail {rail_text!r}, legend {legend_text!r}")

    # And the finished well is counted exactly once, under whatever that word is.
    assert re.search(r"\w+:\s*1", rail_text)


def test_done_wells_carry_a_marker_not_just_colour(qapp):
    """Regression guard (UX audit P2-12): "done" painted colour only --
    accessibility-unreadable to anyone who can't distinguish success-green
    from the other states' colours.

    A *writing* well carries no glyph: it is drawn as a progress dial, and a
    glyph sits in the middle of exactly the area the dial fills."""
    from widgets.well_plate_selector import _parse_well_id

    d = ImportLooseDashboard()
    d.set_wells(["A01", "B02"])
    d.set_well_stage("A01", "writing")
    assert d._plate._grid._markers.get(_parse_well_id("A01")) is None
    d.set_well_stage("A01", "done")
    assert d._plate._grid._markers.get(_parse_well_id("A01")) == "✓"
    d.set_well_stage("B02", "pending")
    assert d._plate._grid._markers.get(_parse_well_id("B02")) is None


def test_unknown_stage_and_well_are_ignored(qapp):
    d = ImportLooseDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "not-a-real-stage")
    assert d._well_stage["A01"] == "pending"
    d.set_well_stage("not-a-well", "done")  # no KeyError, silently ignored
