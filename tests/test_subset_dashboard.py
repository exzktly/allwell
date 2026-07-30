"""Bare-`QApplication` widget tests for `datasets.subset.dashboard.SubsetDashboard`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.subset.dashboard import SubsetDashboard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_wells_and_stage_updates_overall(qapp):
    d = SubsetDashboard()
    d.set_wells(["A01", "B02"])
    assert d.overall_maximum() == 2
    d.set_well_stage("A01", "done")
    d.set_well_stage("B02", "empty")
    assert d._bar.value() == 2


def test_reset_clears_state(qapp):
    d = SubsetDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "done")
    d.reset()
    assert d._wells == []
    assert d._bar.value() == 0
