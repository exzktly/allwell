"""The Nanowells band's **Values** row and its Display tab's one **Display range** row (``SURFACES_TRACKER.md`` S19).

The six settings of which nanowells count and how their values are normalised — Baseline and its average, Rim
focus, Fixed cohort, the moving average and Responding above — moved off the band onto the Values page of the Data
tab, reached from one row that reads what is in force. The four *Range* buttons beside each picture became one row
of the page's Display tab. Driven through the real page builder in the minimal shell of ``test_nanowell_page.py``:
the row is pressed, the page's own controls are changed, and what the row reads and what the inspector shows is read
back.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from review.core import inspector_service as svc  # noqa: E402
from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.values_view import is_offered, values_summary  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401


def test_the_summary_names_the_baseline_then_each_setting_away_from_its_default():
    assert values_summary(baseline="Earliest", rim_focus=False, cohort=False, moving=1, centred=False,
                          baseline_moving=1, responding="") == "Earliest"
    assert values_summary(baseline="Earliest", rim_focus=True, cohort=True, moving=3, centred=True,
                          baseline_moving=2, responding="1.5") == (
        "Earliest (average 2) · rim focus · fixed cohort · centred average 3 · responding > 1.5")
    # A setting the open view does not use says nothing, whatever it holds.
    assert values_summary(baseline=None, rim_focus=False, cohort=False, moving=None, centred=False,
                          baseline_moving=None, responding=None) == ""


def test_the_values_row_reads_what_is_in_force_and_opens_the_page_holding_the_settings(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    nwc.set_view(page, "distribution")
    row = page.widgets.get("_nw_values_btn")
    baseline = page.widgets.get("_nw_baseline_cb")
    rim = page.widgets.get("_nw_rim_focus_box")
    assert row.isVisibleTo(page.window)
    # The dataset's QC turns Rim focus on, and the row says so: it is in force.
    assert rim.isChecked() and row.text() == f"Values: {baseline.currentText()} · rim focus ›"
    # A setting changed on the page's own control is read into the row.
    rim.setChecked(False)
    assert row.text() == f"Values: {baseline.currentText()} ›"
    page.widgets.get("_nw_cohort_box").setChecked(True)
    assert row.text() == f"Values: {baseline.currentText()} · fixed cohort ›"
    # The row opens the Values page in the inspector's Data tab, and the settings are on it.
    qtbot.mouseClick(row, Qt.LeftButton)
    inspector = svc.inspector(page)
    for name in ("_nw_baseline_cb", "_nw_rim_focus_box", "_nw_cohort_box", "_nw_moving_spin",
                 "_nw_baseline_moving_spin", "_nw_responding_edit"):
        widget = page.widgets.get(name)
        assert inspector.isAncestorOf(widget), name
    assert page.widgets.get("_nw_responding_edit").isVisibleTo(inspector)       # Distribution counts against it


def test_the_values_row_is_not_on_a_view_that_draws_no_value(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    for view in ("qc", "identity"):
        nwc.set_view(page, view)
        assert not page.widgets.get("_nw_values_btn").isVisibleTo(page.window), view
        assert not is_offered(page, page.widgets.get("_nw_baseline_cb")), view
    nwc.set_view(page, "trajectories")
    assert page.widgets.get("_nw_values_btn").isVisibleTo(page.window)
    # Trajectories counts no threshold: its line of the page is not offered there.
    assert not is_offered(page, page.widgets.get("_nw_responding_edit"))


def test_the_display_tab_holds_the_one_display_range_row(page, qtbot):  # noqa: F811
    from review.core import inspector_panels as panels
    from review.features.nanowell.display_range_view import DISPLAY_RANGE_BUTTONS

    _loaded(qtbot, page)
    assert DISPLAY_RANGE_BUTTONS == ("_nw_display_range_btn",)
    display = page.widgets.get("_nw_display_btn")
    panel = panels.panel_of(display)
    assert panel is not None and panel.root and panel.tab == "display"
    rng = page.widgets.get("_nw_display_range_btn")
    assert panel.content.isAncestorOf(rng)
    assert rng.text() == "Display range: auto ›"
    qtbot.mouseClick(display, Qt.LeftButton)
    qtbot.mouseClick(rng, Qt.LeftButton)
    assert svc.is_showing(page, page.widgets.get("_nw_range_page"))
    nwc.set_display_range(page, 2.0, 50.0)
    assert rng.text() == "Display range: 2–50 ›"
