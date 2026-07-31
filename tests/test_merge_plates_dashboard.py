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


def test_dataset2_wells_use_accent2_not_the_anomaly_accent_colour(qapp):
    """Regression guard: a merged dataset-2 well used to paint with
    theme.Colors.accent -- the same hue Concatenate uses for "merged, no CSV
    on one side" and Subset uses for "no rows matched the filter". A well
    that's simply "the other dataset" isn't an anomaly and must not share
    that colour with two dashboards that mean something is wrong."""
    import theme

    d = MergePlatesDashboard()
    d.set_wells(["A01"], ["C03"])
    d.set_well_stage("A01", "merged")
    d.set_well_stage("C03", "merged")
    assert d._well_color("A01") == theme.Colors.success
    assert d._well_color("C03") == theme.Colors.accent2
    assert d._well_color("C03") != theme.Colors.accent


def test_dataset2_wells_carry_a_marker_independent_of_colour(qapp):
    """A dataset-2 well's origin must be readable from more than colour alone
    (e.g. for anyone who can't distinguish success-green from accent2-lilac)
    -- it keeps its "2" marker whether pending or merged; only the transient
    "merging" glyph takes priority while a well is actively being copied."""
    d = MergePlatesDashboard()
    d.set_wells(["A01"], ["C03"])
    assert d._well_marker("A01") is None
    assert d._well_marker("C03") == "2"
    d.set_well_stage("C03", "merging")
    assert d._well_marker("C03") == "⚙"
    d.set_well_stage("C03", "merged")
    assert d._well_marker("C03") == "2"


def test_reset_clears_state(qapp):
    d = MergePlatesDashboard()
    d.set_wells(["A01"], ["C03"])
    d.set_well_stage("A01", "merged")
    d.reset()
    assert d._wells == []
    assert d._well_origin == {}
