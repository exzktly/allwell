"""Bare-`QApplication` widget tests for
`datasets.import_loose.dashboard.ImportLooseDashboard`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def test_unknown_stage_and_well_are_ignored(qapp):
    d = ImportLooseDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "not-a-real-stage")
    assert d._well_stage["A01"] == "pending"
    d.set_well_stage("not-a-well", "done")  # no KeyError, silently ignored
