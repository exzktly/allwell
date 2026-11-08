"""The Heat Map's Layout panel: a right-dock panel opened by the band's toggle.

The layout editor — grid size, **Auto-populate from selection**, **Clear** and
the grid wells are dragged into — lives in the plot's right dock, the slide-out
the Properties panel shares, and the control band carries one **Layout**
toggle for it. These tests drive the controls a user drives: they press the
toggle, the panel's ✕, the panel's own buttons, a spin box, a double-click and
a drop on the grid, and assert what reached the layout model and the screen.

Most build the real panel (``build_heatmap_layout_editor``) into a real right
dock (``make_plot_with_right_dock``) over a stub context whose only fakes are
the layout store and the persistence call. The last builds the whole app and
goes through ``build_heatmap_tab`` and the tab's activation hook, because the
panel opening itself on an empty grid is decided there and nowhere a stub sees.
"""

from __future__ import annotations

import types

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QStackedWidget, QWidget

from review.core.dnd import WELL_MIME
from review.core.widget_registry import WidgetRegistry
from review.features.heatmap import layout_view as LV
from review.features.heatmap.models import HeatmapLayout


def _rig(qtbot, *, cells=None, wells=("A01", "A02", "B01", "B02")):
    """A real dock, band parent and Layout editor over a stub context."""
    from review.core.ui_helpers import make_plot_with_right_dock

    layout = HeatmapLayout(name=LV.SIDEBAR_LAYOUT_NAME, rows=8, cols=12)
    for (r, c), ws in (cells or {}).items():
        layout.assign(r, c, list(ws))
    saved: list[str] = []
    ctx = types.SimpleNamespace(
        widgets=WidgetRegistry(),
        runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(layouts=[layout])),
        state=types.SimpleNamespace(
            heatmap=types.SimpleNamespace(
                active_layout_name=LV.SIDEBAR_LAYOUT_NAME, repset_avg=False),
            dataset=types.SimpleNamespace(well_paths={w: f"/x/{w}.zip" for w in wells}),
        ),
        persistence=types.SimpleNamespace(schedule_save=saved.append),
    )
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(1200, 800)
    _plot_area, _plot_layout, dock = make_plot_with_right_dock(host)
    ctx.widgets.register("_heatmap_export_dock", dock)
    band = QWidget(host)
    button = LV.build_heatmap_layout_editor(ctx, band, dock)
    host.show()
    qtbot.waitExposed(host)
    panel = ctx.widgets.get("_heatmap_layout_panel")
    return types.SimpleNamespace(ctx=ctx, layout=layout, dock=dock, button=button,
                                 panel=panel, saved=saved, host=host)


def _panel_button(panel, text: str) -> QPushButton:
    found = [b for b in panel.findChildren(QPushButton) if b.text() == text]
    assert len(found) == 1, f"the panel should carry one {text!r} button, found {found}"
    return found[0]


# ── the toggle ───────────────────────────────────────────────────────────────

def test_the_band_toggle_opens_the_panel_in_the_dock_and_closes_it_again(qtbot):
    rig = _rig(qtbot)
    assert rig.button.text() == "Layout"
    assert not rig.panel.isVisible() and not rig.button.isChecked()

    rig.button.click()
    assert rig.dock.isVisible(), "the toggle must show the plot's right dock"
    assert rig.panel.isVisible(), "…with the Layout panel in it"
    assert rig.button.isChecked()

    rig.button.click()
    assert not rig.dock.isVisible()
    assert not rig.button.isChecked()


def test_the_toggle_unchecks_when_the_panel_s_own_close_button_hides_it(qtbot):
    from widgets.icon_button import IconButton

    rig = _rig(qtbot)
    rig.button.click()
    close = [b for b in rig.panel.findChildren(IconButton) if b.iconName() == "x"]
    assert len(close) == 1
    close[0].click()
    assert not rig.dock.isVisible()
    assert not rig.button.isChecked(), (
        "the toggle still reads 'open' after the panel's ✕ closed it")


def test_the_toggle_unchecks_when_properties_takes_the_dock(qtbot):
    """Properties and Layout share one dock and show one at a time."""
    from review.core.ui_helpers import mount_widget_in_plot_dock

    rig = _rig(qtbot)
    rig.button.click()
    other = QWidget()
    mount_widget_in_plot_dock(rig.dock, other, 0)
    assert other.isVisible() and not rig.panel.isVisible()
    assert not rig.button.isChecked()
    # And pressing the toggle again brings Layout back in Properties' place.
    rig.button.click()
    assert rig.panel.isVisible() and not other.isVisible()
    assert rig.button.isChecked()


def test_the_toggle_is_an_action_in_phone_layout_s_controls_sheet(qtbot):
    from widgets.control_band_form import section_of_action

    rig = _rig(qtbot)
    assert section_of_action(rig.button) == "Actions"


# ── the panel's own controls ────────────────────────────────────────────────

def test_auto_populate_in_the_panel_lays_the_selection_into_the_grid(qtbot, monkeypatch):
    from review.data import data_loading

    rig = _rig(qtbot)
    # The selection, as the one producer answers it: two solo wells, out of
    # plate order, so a fill in the order given and a fill in plate order differ.
    monkeypatch.setattr(
        data_loading, "plot_group_well_lists",
        lambda _ctx, fallback_to_all=True: [("B02", ["B02"], None), ("A01", ["A01"], None)])
    rig.button.click()
    _panel_button(rig.panel, "Auto-populate from selection").click()

    assert rig.layout.cells == {(0, 0): ["A01"], (0, 1): ["B02"]}
    table = rig.ctx.widgets.get("_heatmap_layout_table")
    assert [table.item(0, c).text() for c in range(3)] == ["A01", "B02", ""]
    assert rig.saved == ["heatmap_layouts"]


def test_clear_empties_every_cell_and_keeps_the_size_and_the_names(qtbot):
    rig = _rig(qtbot, cells={(0, 0): ["A01"], (2, 3): ["B01", "B02"]})
    rig.layout.resize(6, 10)
    rig.layout.row_labels = [f"r{i}" for i in range(6)]
    LV.refresh_heatmap_layout_panel(rig.ctx)
    _panel_button(rig.panel, "Clear").click()

    assert rig.layout.cells == {}
    assert (rig.layout.rows, rig.layout.cols) == (6, 10)
    assert rig.layout.row_labels == [f"r{i}" for i in range(6)]
    table = rig.ctx.widgets.get("_heatmap_layout_table")
    assert all(table.item(r, c).text() == "" for r in range(6) for c in range(10))
    assert rig.saved == ["heatmap_layouts"]


def test_the_size_boxes_resize_the_layout(qtbot):
    rig = _rig(qtbot, cells={(0, 0): ["A01"]})
    rows = rig.ctx.widgets.get("_heatmap_layout_rows_spin")
    cols = rig.ctx.widgets.get("_heatmap_layout_cols_spin")
    rows.setValue(3)
    cols.setValue(5)
    assert (rig.layout.rows, rig.layout.cols) == (3, 5)
    table = rig.ctx.widgets.get("_heatmap_layout_table")
    assert (table.rowCount(), table.columnCount()) == (3, 5)
    assert rig.layout.cells == {(0, 0): ["A01"]}


def test_double_clicking_a_cell_in_the_panel_empties_it(qtbot):
    rig = _rig(qtbot, cells={(1, 2): ["B01"], (0, 0): ["A01"]})
    rig.button.click()
    table = rig.ctx.widgets.get("_heatmap_layout_table")
    rect = table.visualRect(table.model().index(1, 2))
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert rig.layout.cells == {(0, 0): ["A01"]}
    assert table.item(1, 2).text() == ""


def test_a_well_dropped_on_a_cell_of_the_panel_lands_in_that_cell(qtbot):
    """A drag from the rail plate ends in the table's ``dropEvent``."""
    rig = _rig(qtbot)
    rig.button.click()
    table = rig.ctx.widgets.get("_heatmap_layout_table")
    rect = table.visualRect(table.model().index(2, 4))
    mime = QMimeData()
    mime.setData(WELL_MIME, b"B02")
    ev = QDropEvent(QPointF(rect.center()), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    table.dropEvent(ev)
    qtbot.waitUntil(lambda: rig.layout.cells == {(2, 4): ["B02"]}, timeout=2000)
    assert table.item(2, 4).text() == "B02"


def test_a_whole_plate_shows_in_the_dock_without_scrolling(qtbot):
    """The dock is ``Metrics.dock_w`` wide: an 8×12 plate fits it whole."""
    rig = _rig(qtbot)
    rig.button.click()
    table = rig.ctx.widgets.get("_heatmap_layout_table")
    qtbot.waitUntil(lambda: table.width() > 0)
    assert not table.horizontalScrollBar().isVisible()
    assert not table.verticalScrollBar().isVisible()
    last = table.visualRect(table.model().index(7, 11))
    assert table.viewport().rect().contains(last), (
        f"cell H12 {last} falls outside the grid's viewport {table.viewport().rect()}")
    # And the cells reach the frame: no strip of bare header past column 12.
    assert last.right() >= table.viewport().rect().right() - 1


# ── opening with the page ───────────────────────────────────────────────────

def test_the_panel_opens_with_the_page_while_the_grid_is_empty(qtbot):
    rig = _rig(qtbot)
    LV.open_layout_panel_if_empty(rig.ctx)
    assert rig.panel.isVisible() and rig.button.isChecked()


@pytest.mark.parametrize("why", ["filled", "no dataset", "phone"])
def test_the_panel_stays_shut_when_the_page_has_something_else_to_show(qtbot, monkeypatch, why):
    from core import phone_layout

    rig = _rig(qtbot, cells={(0, 0): ["A01"]} if why == "filled" else None,
               wells=() if why == "no dataset" else ("A01",))
    if why == "phone":
        monkeypatch.setattr(phone_layout, "is_on", lambda: True)
    LV.open_layout_panel_if_empty(rig.ctx)
    assert not rig.panel.isVisible()
    assert not rig.dock.isVisible()


# ── through the real tab ────────────────────────────────────────────────────

@pytest.mark.slow
def test_the_real_heat_map_page_opens_its_layout_panel_and_fills_from_it(loaded_app):
    """``build_heatmap_tab`` and the tab's activation, as the app runs them.

    The fixture's grid starts empty, so showing the page opens the panel; its
    Auto-populate button then fills the grid and the figure replaces the
    placeholder; the band's toggle closes the panel again.
    """
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app
    ctx = app._ctx
    assert app._notebook.setCurrentByName("Plotting")
    app._drain_pending_centre_builders()
    app._plotting_notebook.setCurrentByName("Heat Map")
    app._drain_pending_centre_builders()

    button = ctx.widgets.get("_heatmap_layout_button")
    panel = ctx.widgets.get("_heatmap_layout_panel")
    assert not LV._ensure_sidebar_layout(ctx).cells
    # The fixture's window is never shown, so "on screen" is visibility
    # relative to the window: what the user would see once it is.
    assert panel.isVisibleTo(app), "an empty grid must put the Layout panel in front"
    assert button.isChecked()

    app._set_selected_wells(set(WELLS), commit=True)
    _panel_button(panel, "Auto-populate from selection").click()
    placed = sorted(w for ws in LV._ensure_sidebar_layout(ctx).cells.values() for w in ws)
    assert placed == sorted(WELLS)

    stack = ctx.widgets.get("_heatmap_canvas")
    while stack is not None and not (
        isinstance(stack, QStackedWidget) and stack.objectName() == "PlotEmptyStateStack"
    ):
        stack = stack.parentWidget()
    assert stack is not None and stack.currentIndex() == 1

    button.click()
    assert not panel.isVisibleTo(app) and not button.isChecked()
