"""ConcatDashboard + the shared PlateProgress base it's built on.

Bare-``QApplication`` widget tests, the same pattern
``tests/test_run_dashboard.py`` uses -- never the ``viewer_app``/``loaded_app``
full-app fixtures (CLAUDE.md §2's per-test RSS leak).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QSizePolicy


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_plate_vertical_size_policy_is_not_fixed(qapp):
    """Regression guard: PlateProgress._plate used to be pinned to
    QSizePolicy.Fixed vertically, which locks the widget's rendered height
    at its own small sizeHint regardless of how much room the row actually
    has. Since well_plate_selector._metrics() derives cell size from
    min(available_width / 12, available_height / 8), a Fixed-height plate
    starves the height side of that min() even when the row is hundreds of
    pixels wide, rendering a dashboard's plate map illegibly small at the
    app's real default window size (1640x980 - allwell.py) — every
    PlateProgress subclass (RunDashboard, ConcatDashboard, SubsetDashboard,
    MergePlatesDashboard, ValidateDashboard) shares the one base class, so
    one policy fix (widgets/plate_progress.py) covers all of them."""
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    assert d._plate.sizePolicy().verticalPolicy() != QSizePolicy.Fixed


def test_plate_has_a_matching_width_cap(qapp):
    """Regression guard: well_plate_selector.WellPlateSelector paints a
    panel background + border across its *entire* allocated widget box
    (its #WellPlateSelector QSS rule), independent of how much of that box
    the grid itself actually draws into (well_plate_selector._metrics()
    sizes the grid to min(avail_w/12, avail_h/8)). PlateProgress's centre
    column is shared with a fixed-width rail + legend but is otherwise as
    wide as the host page allows, so once the plate's maximumHeight caps the
    grid's cell size, any extra width the layout still hands the plate
    widget doesn't grow the grid at all — it only stretches the panel's own
    background into a big, mostly-empty grey card with a small grid
    centred inside it. A matching maximumWidth keeps the card hugging its
    content instead."""
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    assert d._plate.maximumWidth() < 500
    assert d._plate.maximumHeight() < 500


def test_run_dashboard_still_exposes_every_test_facing_attribute(qapp):
    """Regression guard for the PlateProgress extraction: every attribute
    tests/test_run_dashboard.py pokes must still resolve on RunDashboard."""
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    # The ``_fov_*`` scroll area that once held a progress bar per computing
    # well is deliberately absent from this list: the per-well rail is gone,
    # its information having moved onto the plate's dials.
    for attr in ("_plate", "_body",
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


def test_terminal_well_states_carry_a_marker_not_just_colour(qapp):
    """Regression guard (UX audit P2-12): "merged" and "warned" painted
    colour only -- accessibility-unreadable to anyone who can't distinguish
    success-green from accent-lilac, and the one state (warned) that means
    "look at this, something's off" had no glyph at all once the transient
    "⚙" cleared."""
    from datasets.concatenate.dashboard import ConcatDashboard
    from widgets.well_plate_selector import _parse_well_id

    d = ConcatDashboard()
    d.set_wells(["A01", "A02", "A03"])
    d.set_well_stage("A01", "merging")
    assert d._plate._grid._markers.get(_parse_well_id("A01")) == "⚙"
    d.set_well_stage("A01", "merged")
    assert d._plate._grid._markers.get(_parse_well_id("A01")) == "✓"
    d.set_well_stage("A02", "warned")
    assert d._plate._grid._markers.get(_parse_well_id("A02")) == "!"
    d.set_well_stage("A03", "pending")
    assert d._plate._grid._markers.get(_parse_well_id("A03")) is None


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


def test_reset_disables_all_wells_instead_of_a_false_96_well_pending_grid(qapp):
    """Regression guard: reset() used to call setEnabledWells(None), which
    enables all 96 wells -- an enabled-but-uncolored well renders as
    "pending", so the plate misstated scope (implying 96 queued wells) from
    the moment the page loaded, before set_wells() ever narrows it to the
    real, run-specific well list. The same reset() runs on a real planning
    failure that lands before set_wells() arrives too, so a genuine failure
    also used to paint a full plate of phantom pending wells."""
    from datasets.concatenate.dashboard import ConcatDashboard
    d = ConcatDashboard()
    d.reset()
    assert d._plate._grid._enabled == set()


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
