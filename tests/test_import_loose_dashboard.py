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
    "Done: N" while the legend and the well tooltip both call the same state
    "converted" -- two different words for one state, right next to each
    other in the same status strip."""
    d = ImportLooseDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "done")

    rail_text = " ".join(lbl.text() for lbl in d._rail_host.findChildren(QLabel))
    legend_text = " ".join(lbl.text() for lbl in d._legend_host.findChildren(QLabel))
    assert "Done" not in rail_text
    assert "Converted: 1" in rail_text
    assert "converted" in legend_text


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
