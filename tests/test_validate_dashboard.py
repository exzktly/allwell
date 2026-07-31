"""Bare-`QApplication` widget tests for `datasets.validate.dashboard.ValidateDashboard`.

Same pattern as `tests/test_concat_dashboard.py` — never the `viewer_app`/
`loaded_app` full-app fixtures (CLAUDE.md §2's per-test RSS leak).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.validate.dashboard import ValidateDashboard  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_set_wells_starts_all_no_output(qapp):
    d = ValidateDashboard()
    d.set_wells(["A01", "B02"])
    assert d._well_state == {"A01": "no_output", "B02": "no_output"}
    assert d.overall_maximum() == 2


def test_set_well_state_updates_overall_only_on_complete(qapp):
    d = ValidateDashboard()
    d.set_wells(["A01", "B02"])
    d.set_well_state("A01", "complete")
    d.set_well_state("B02", "partial", missing_count=3)
    assert d._bar.value() == 1
    assert "3" in d._well_tooltip("B02")


def test_reset_clears_state(qapp):
    d = ValidateDashboard()
    d.set_wells(["A01"])
    d.set_well_state("A01", "complete")
    d.reset()
    assert d._wells == []
    assert d._well_state == {}
    assert d._bar.value() == 0


def test_complete_wells_carry_a_marker_not_just_colour(qapp):
    """Regression guard (UX audit P2-12): "complete" painted colour only --
    accessibility-unreadable to anyone who can't distinguish success-green
    from the other three states' colours, unlike partial/missing which
    already carry "!"."""
    from widgets.well_plate_selector import _parse_well_id

    d = ValidateDashboard()
    d.set_wells(["A01", "B02", "C03"])
    d.set_well_state("A01", "complete")
    assert d._plate._grid._markers.get(_parse_well_id("A01")) == "✓"
    d.set_well_state("B02", "partial", missing_count=1)
    assert d._plate._grid._markers.get(_parse_well_id("B02")) == "!"
    d.set_well_state("C03", "no_output")
    assert d._plate._grid._markers.get(_parse_well_id("C03")) is None


def test_unknown_well_and_state_are_ignored(qapp):
    d = ValidateDashboard()
    d.set_wells(["A01"])
    d.set_well_state("Z99", "complete")
    d.set_well_state("A01", "bogus_state")
    assert d._well_state == {"A01": "no_output"}
