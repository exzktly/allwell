"""Bare-`QApplication` widget tests for `datasets.merge_plates.dashboard.MergePlatesDashboard`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.merge_plates.dashboard import MergePlatesDashboard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_wells_tracks_origin(qapp):
    d = MergePlatesDashboard()
    d.set_wells(["A01", "B02"], ["C03"])
    assert d._well_origin == {"A01": "ds1", "B02": "ds1", "C03": "ds2"}
    assert d.overall_maximum() == 3


def test_merged_wells_colored_by_origin(qapp):
    d = MergePlatesDashboard()
    d.set_wells(["A01"], ["C03"])
    d.set_well_stage("A01", "merged")
    d.set_well_stage("C03", "merged")
    assert d._well_color("A01") != d._well_color("C03")


def test_reset_clears_state(qapp):
    d = MergePlatesDashboard()
    d.set_wells(["A01"], ["C03"])
    d.set_well_stage("A01", "merged")
    d.reset()
    assert d._wells == []
    assert d._well_origin == {}
