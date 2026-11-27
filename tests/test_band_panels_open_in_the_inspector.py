"""A band's panel buttons, pressed for real, show their panels in the inspector.

Every page that used to drop a popover from its band is built here through the
registry's own builder, and the **real button is clicked**. Three things are
asserted at the boundary each time (CLAUDE.md §7.4 #1, §7.6 #1): the inspector
is open on that panel; a control inside the panel, when changed, reaches the
state the plot is drawn from; and no more than one surface is open.

Asserting only that the button is offered is the mistake §7.6 documents, so the
file's first test fails on a page whose buttons press nothing.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton

from review.core import inspector_panels as panels
from review.core import inspector_service as svc
from tests import _surface_checks as surf
from tests._minimal_shell import build_page

#: page -> the titles of the panel buttons its band shows, in row order, with
#: the tab each belongs to. A root panel is the tab's first page.
PANELS = {
    "Bar Plots": [("Fold change", "data", False), ("Display", "display", True)],
    "Line Graphs": [("Fold change", "data", False), ("Display", "display", True)],
    "Response Curves": [("Fit", "data", False), ("Fold change", "data", False),
                        ("Display", "display", True)],
    "Heat Map": [("Display", "display", True)],
    "Multivariate": [("Features", "data", False)],
}


def _buttons(ctx, window) -> list[QToolButton]:
    """The panel buttons on screen, in the order the row holds them."""
    found = [b for b in window.findChildren(QToolButton)
             if panels.panel_of(b) is not None and b.isVisibleTo(window)]
    return sorted(found, key=lambda b: b.mapTo(window, b.rect().topLeft()).x())


@pytest.mark.parametrize("name", sorted(PANELS))
def test_each_panel_button_opens_its_panel_in_the_inspector(name, qtbot, mini_dataset):
    ctx, window, _frame, _bands = build_page(qtbot, name, mini_dataset)
    buttons = _buttons(ctx, window)
    assert [(panels.panel_of(b).title, panels.panel_of(b).tab, panels.panel_of(b).root)
            for b in buttons] == PANELS[name]
    insp = svc.inspector(ctx)
    for b in buttons:
        panel = panels.panel_of(b)
        svc.close(ctx)                      # each press starts from a closed inspector
        b.click()
        QApplication.processEvents()
        assert svc.is_open(ctx), f"{name}: pressing {panel.title!r} opened nothing"
        assert insp.currentTab() == panel.tab
        shown = insp.tabWidget(panel.tab) if panel.root else insp.currentWidget()
        assert shown is panel.content, f"{name}: the inspector is not on {panel.title!r}"
        assert panel.content.isVisibleTo(window)
        assert surf.depth(window) <= 1, surf.describe(surf.open_transients(window))
        # Pressing the same button again leaves: a row's page is popped, and a
        # tab's own button closes the inspector.
        b.click()
        QApplication.processEvents()
        if panel.root:
            assert not svc.is_open(ctx)
        assert insp.currentWidget() is not panel.content or not svc.is_open(ctx)
        assert panel.root or not insp.holds(panel.content)
    svc.close(ctx)
    assert not any(insp.holds(panels.panel_of(b).content) for b in buttons)


def test_a_row_reads_its_value_and_ends_in_a_chevron(qtbot, mini_dataset):
    ctx, window, _frame, _bands = build_page(qtbot, "Multivariate", mini_dataset)
    (features,) = _buttons(ctx, window)
    assert features.text().endswith("›") and "▾" not in features.text()
    assert features.menu() is None


# ── a control inside the page reaches the state the plot is drawn from ────────

def test_bar_display_s_grouping_toggle_inside_the_inspector_changes_the_bar_state(qtbot, mini_dataset):
    """The Display tab's own control, pressed where the user now finds it."""
    ctx, window, _frame, _bands = build_page(qtbot, "Bar Plots", mini_dataset)
    display = next(b for b in _buttons(ctx, window) if panels.panel_of(b).root)
    display.click()
    QApplication.processEvents()
    insp = svc.inspector(ctx)
    grouping = ctx.widgets.get("_bar_group_mode_btn")
    assert insp.isAncestorOf(grouping) and grouping.isVisibleTo(window)
    before = grouping.text()
    grouping.click()
    QApplication.processEvents()
    assert grouping.text() != before and grouping.text().startswith("Grouping: ")
    assert surf.depth(window) == 0, "the desk inspector is a column, not a surface over the page"


def test_line_fold_change_inside_the_inspector_writes_the_shared_fold_change_state(qtbot, mini_dataset):
    """Fold change is a Data page; choosing a baseline in it reaches
    ``state.plot.fc_vs_t0_on`` exactly as the popover's combo did — and the
    row behind it then reads the choice."""
    from review.core.fold_change_controls_view import _EARLIEST_LABEL
    ctx, window, _frame, _bands = build_page(qtbot, "Line Graphs", mini_dataset)
    fold = next(b for b in _buttons(ctx, window) if panels.panel_of(b).title == "Fold change")
    fold.click()
    QApplication.processEvents()
    content = panels.panel_of(fold).content
    baseline = next(c for c in content.findChildren(QComboBox) if type(c).__name__ == "_BaselineCombo")
    assert baseline.isVisibleTo(window), "the Fold change page does not show its baseline control"
    assert ctx.state.plot.fc_vs_t0_on is False
    baseline.setCurrentIndex(baseline.findText(_EARLIEST_LABEL))
    QApplication.processEvents()
    assert ctx.state.plot.fc_vs_t0_on is True
    assert svc.inspector(ctx).currentWidget() is content, "changing a control left the page"


def test_a_page_change_gives_the_panel_back_to_its_button(qtbot, mini_dataset, monkeypatch):
    ctx, window, _frame, _bands = build_page(qtbot, "Heat Map", mini_dataset)
    (display,) = _buttons(ctx, window)
    display.click()
    content = panels.panel_of(display).content
    assert svc.inspector(ctx).holds(content)
    monkeypatch.setattr(svc, "current_leaf", lambda _ctx: "Bar Plots")
    display.hide()                      # the leaf's slot is no longer the one on show
    svc.sync(ctx)
    assert not svc.inspector(ctx).holds(content)
    assert content.parentWidget() is display and content.isHidden()


# ── Style: the figure's export look, as the inspector's Style tab ─────────────

def _style_button(window):
    return next(b for b in window.findChildren(QToolButton)
                if b.property("bandRole") == "style" and b.isVisibleTo(window) and b.isEnabled())


def test_style_opens_the_figure_s_panel_as_the_style_tab_and_a_second_press_closes_it(qtbot, mini_dataset):
    from review.features.export.properties_view import ExportStyleSidebar
    ctx, window, _frame, _bands = build_page(qtbot, "Bar Plots", mini_dataset)
    style = _style_button(window)
    style.click()
    QApplication.processEvents()
    insp = svc.inspector(ctx)
    panel = insp.tabWidget("style")
    assert svc.is_open(ctx) and insp.currentTab() == "style"
    assert isinstance(panel, ExportStyleSidebar) and panel.isVisibleTo(window)
    assert panel.width() <= insp.width(), "the panel is wider than the inspector that shows it"
    assert surf.depth(window) == 0
    style.click()
    QApplication.processEvents()
    assert not svc.is_open(ctx)
    assert not insp.holds(panel) and panel.isHidden()


def test_a_style_control_changed_on_its_page_restyles_the_figure(qtbot, mini_dataset):
    """Style › Grid › Show grid, pressed through the real rows: the assertion
    is on the figure's own grid lines, not on the switch."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from widgets.collapsible_section import CollapsibleSection
    from widgets.toggle_switch import ToggleSwitch
    ctx, window, _frame, _bands = build_page(qtbot, "Bar Plots", mini_dataset)
    fig = ctx.widgets.get("_bar_fig")

    def grid_on() -> bool:
        return any(line.get_visible() for ax in fig.axes for line in ax.get_xgridlines() + ax.get_ygridlines())

    _style_button(window).click()
    QApplication.processEvents()
    insp = svc.inspector(ctx)
    panel = insp.tabWidget("style")
    grid = next(sec for sec in panel.findChildren(CollapsibleSection) if sec.titleText() == "Grid")
    assert grid.isVisibleTo(window) and not grid.body().isVisibleTo(window)
    QTest.mouseClick(grid._header, Qt.LeftButton, pos=QPoint(8, 8))
    QApplication.processEvents()
    assert insp.currentWidget() is grid.body() and insp.depth() == 1
    assert insp.title_label.text() == "Grid" and insp.crumb_label.text() == "Style"
    switch = next(t for t in grid.body().findChildren(ToggleSwitch) if t.text() == "Show grid")
    assert switch.isVisibleTo(window)
    before = grid_on()
    switch.setChecked(not switch.isChecked())
    QApplication.processEvents()
    assert grid_on() is (not before), "the figure did not restyle while the Style page was open"
    insp.back_button.click()
    assert insp.currentWidget() is panel and grid.body().isHidden()
    svc.close(ctx)


# ── picking several: one page, applied as a box is ticked ─────────────────────

def test_bar_timepoints_is_a_multi_select_page_and_a_tick_reaches_the_bar_state(qtbot, mini_dataset):
    """The Timepoints button, pressed for real: a page of the inspector, not
    a menu of checkboxes — and unticking a box changes
    ``state.bar.tp_selections`` at once, with the button reading the count."""
    from widgets.multi_select import MultiSelect
    ctx, window, _frame, _bands = build_page(qtbot, "Bar Plots", mini_dataset)
    button = ctx.widgets.get("_bar_tp_button")
    # Seeded out of numeric order, so "sorted as numbers" and "as given" differ.
    ctx.state.bar.tp_selections.clear()
    ctx.state.bar.tp_selections.update({"24": True, "0": True, "6": True})
    sels = ctx.state.bar.tp_selections
    button.click()
    QApplication.processEvents()
    insp = svc.inspector(ctx)
    picker = insp.currentWidget()
    assert isinstance(picker, MultiSelect) and picker.isVisibleTo(window)
    assert insp.title_label.text() == "Timepoints"
    assert surf.depth(window) == 0, "a menu or popup opened as well: " + surf.describe(surf.open_transients(window))
    order = sorted(sels, key=float)
    assert picker.keys() == order and picker.checked() == order
    picker.boxes()[0].click()
    QApplication.processEvents()
    assert sels[order[0]] is False and all(sels[tp] for tp in order[1:])
    picker.none_button.click()
    assert not any(sels.values())
    button.click()                                     # the button is a toggle
    QApplication.processEvents()
    assert not insp.holds(picker), "a second press on the button did not leave the page"
