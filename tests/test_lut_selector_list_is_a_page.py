"""The colour-map picker's searchable list is a page of the inspector.

It was a popover dropped from the selector, which on the Heat Map sat inside
the Display popover: two surfaces deep. The selector opens nothing itself now;
it hands its list to a host. Driven through the Heat Map's real builder and the
real trigger, and the assertion is on the colour map the figure is drawn in.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from review.core import inspector_panels as panels
from review.core import inspector_service as svc
from tests import _surface_checks as surf
from tests._minimal_shell import build_page


def test_picking_a_map_through_the_page_changes_the_figure_s_colour_map(qtbot, mini_dataset):
    ctx, window, _frame, _bands = build_page(qtbot, "Heat Map", mini_dataset)
    display = next(b for b in window.findChildren(QToolButton)
                   if panels.panel_of(b) is not None and panels.panel_of(b).root and b.isVisibleTo(window))
    display.click()
    QApplication.processEvents()
    selector = ctx.widgets.get("_heatmap_cmap_cb")
    assert selector.isVisibleTo(window), "the Display tab does not show the colour-map selector"
    before = selector.lut()
    QTest.mouseClick(selector._trigger, Qt.LeftButton, pos=QPoint(4, 4))
    QApplication.processEvents()
    insp = svc.inspector(ctx)
    listing = selector.listWidget()
    assert insp.currentWidget() is listing and insp.depth() == 1
    assert insp.title_label.text() == "Colour map" and insp.crumb_label.text() == "Display"
    assert surf.depth(window) == 0, "the list opened a surface: " + surf.describe(surf.open_transients(window))
    target = next(row for row in selector._rows if row.name() not in (before,) and row.name() == "magma")
    assert target.isVisibleTo(window)
    seen: list[tuple] = []
    selector.lutChanged.connect(lambda name, rev: seen.append((name, rev)))
    QTest.mouseClick(target, Qt.LeftButton, pos=QPoint(4, 4))
    QApplication.processEvents()
    assert seen and seen[-1][0] == "magma" and selector.lut() == "magma"
    assert insp.depth() == 0 and insp.currentTab() == "display", "picking a map did not leave the list"
    assert not insp.holds(listing)


def test_the_search_narrows_the_list_on_the_page(qtbot, mini_dataset):
    ctx, window, _frame, _bands = build_page(qtbot, "Heat Map", mini_dataset)
    selector = ctx.widgets.get("_heatmap_cmap_cb")
    selector._open_list()
    QApplication.processEvents()
    selector._search.setText("virid")
    shown = [row.name() for row in selector._rows if not row.isHidden()]
    assert shown and all("virid" in name.lower() for name in shown)
    assert len(shown) < len(selector._rows)
