"""ConcatDashboard + the shared PlateProgress base it's built on.

Bare-``QApplication`` widget tests, the same pattern
``tests/test_run_dashboard.py`` uses -- never the ``viewer_app``/``loaded_app``
full-app fixtures (CLAUDE.md §2's per-test RSS leak).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_run_dashboard_still_exposes_every_test_facing_attribute(qapp):
    """Regression guard for the PlateProgress extraction: every attribute
    tests/test_run_dashboard.py pokes must still resolve on RunDashboard."""
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    for attr in ("_plate", "_fov_lay", "_fov_scroll", "_fov_host", "_body",
                 "_toggle", "_rail", "_headline", "_bar"):
        assert hasattr(d, attr), f"RunDashboard lost attribute {attr!r}"
    assert callable(d._count)
    assert callable(d._well_color)
    assert callable(d._well_tooltip)


def test_dashboard_set_wells_enables_only_participating_wells(qapp):
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    d.set_wells(["A01", "b2", "not-a-well"])
    assert d._wells == ["A01", "B02"]
    assert set(d._well_stage) == {"A01", "B02"}
    assert all(s == "pending" for s in d._well_stage.values())


def test_dashboard_well_stage_updates_color_and_overall_bar(qapp):
    from datasets.concatenate.dashboard import ConcatDashboard
    import theme
    d = ConcatDashboard()
    d.set_wells(["A01", "A02"])
    d.set_well_stage("A01", "merging")
    assert d._well_color("A01") == theme.Colors.warn
    d.set_well_stage("A01", "merged")
    assert d._well_color("A01") == theme.Colors.success
    assert d.overall_maximum() == 2
    assert d._bar.value() == 1
    d.set_well_stage("A02", "warned")
    assert d._well_color("A02") == theme.Colors.accent
    assert d._bar.value() == 2


def test_dashboard_no_per_well_failed_state(qapp):
    """There is no reachable per-well failure -- an unknown stage is ignored,
    not painted red (a well-loop exception aborts the whole run instead)."""
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "failed")   # not a recognised per-well stage
    assert d._well_stage["A01"] == "pending"


def test_dashboard_set_failed_is_visible_without_the_raw_log(qapp):
    """The regression this dashboard exists to prevent: a run-level failure
    (e.g. a planning-stage error, which fires before any well is known) must
    show up on the headline even though the raw log is collapsed by default."""
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    d.reset()
    d.set_failed("The two datasets were not analysed the same way.")
    assert "Failed" in d._headline.text()
    assert "not analysed the same way" in d._headline.text()
    assert d._outcome == "failed"


def test_dashboard_set_cancelled_is_distinct_from_failed(qapp):
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    d.reset()
    d.set_cancelled("left in place to resume later")
    assert "Cancelled" in d._headline.text()
    assert d._outcome == "cancelled"


def test_dashboard_reset_clears_previous_run_state(qapp):
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    d.set_wells(["A01"])
    d.set_well_stage("A01", "merged")
    d.set_failed("boom")
    d.reset()
    assert d._wells == []
    assert d._outcome == ""
    assert d._headline.text() == "Idle"


def test_dashboard_phase_rail_marks_earlier_phases_done(qapp):
    from datasets.concatenate.dashboard import ConcatDashboard
    import theme
    d = ConcatDashboard()
    d.reset()
    d.set_phase("verify")
    dot, _nm, _bar, _cnt = d._rail["validate"]
    assert "background:" + theme.Colors.success in dot.styleSheet()
    dot_active, _nm2, _bar2, _cnt2 = d._rail["verify"]
    assert "background:" + theme.Colors.warn in dot_active.styleSheet()
