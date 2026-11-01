"""One selection footer under every multi-select well picker on the rail.

The rail's two multi-select pickers said the same thing in different words: the
plotting pages had one **Deselect all** button and no count, the Image Table had
**Select All** / **Clear** and "N wells active", and the manual promised
**Select all** / **Select none**. Both now end in ``widgets.SelectionFooter`` —
a count, **All**, **None** — and Saved selections sits directly under it
instead of behind a stretch at the rail's foot.

These drive the real builders and press the real buttons (CLAUDE.md §7.6): a
footer that is built and wired to nothing would pass any test that only looked
for it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from review.core.widget_registry import WidgetRegistry
from widgets.selection_footer import SelectionFooter

LOADED = ("A01", "B02", "C03")


def _layout_widgets(parent):
    """The parent's layout items in order: a widget, or ``"stretch"``."""
    lay = parent.layout()
    out = []
    for i in range(lay.count()):
        item = lay.itemAt(i)
        out.append(item.widget() if item.widget() is not None else "stretch")
    return out


# ── the widget ──────────────────────────────────────────────────────────────

def test_the_footer_forwards_its_buttons_and_shows_its_count(qtbot):
    footer = SelectionFooter()
    qtbot.addWidget(footer)
    got = []
    footer.selectAllRequested.connect(lambda: got.append("all"))
    footer.selectNoneRequested.connect(lambda: got.append("none"))
    qtbot.mouseClick(footer.allButton(), Qt.LeftButton)
    qtbot.mouseClick(footer.noneButton(), Qt.LeftButton)
    assert got == ["all", "none"]
    assert footer.allButton().text() == "All"
    assert footer.noneButton().text() == "None"
    footer.setText("3 wells selected")
    assert footer.text() == "3 wells selected"
    footer.setAllVisible(False)
    assert footer.allButton().isHidden()


# ── the Image Table's picker ────────────────────────────────────────────────

def _image_table_ctx(groups=()):
    ctx = SimpleNamespace()
    ctx.widgets = WidgetRegistry()
    ctx.state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: None for w in LOADED}),
        image_table=SimpleNamespace(active_wells=set()),
        selection=SimpleNamespace(plot_groups=list(groups)),
    )
    ctx.runtime = SimpleNamespace(
        image_table=SimpleNamespace(sidebar_plate=None, count_lbl=None,
                                    selection_rebuild_cb=None),
        plot_groups=SimpleNamespace(drawers=None),
    )
    ctx.features = SimpleNamespace(query=lambda *a, default=None, **k: default,
                                   rail_sections=lambda: [])
    ctx.notify = SimpleNamespace(set_status=lambda *_a, **_k: None)
    return ctx


@pytest.fixture
def image_table(qtbot):
    from review.features.image_browser.image_table.picker_view import (
        build_image_table_picker,
    )
    groups = [{"id": "g1", "name": "One", "wells": ["A01"]},
              {"id": "g2", "name": "Two", "wells": ["B02"]}]
    ctx = _image_table_ctx(groups)
    parent = QWidget()
    qtbot.addWidget(parent)
    build_image_table_picker(ctx, parent)
    footers = parent.findChildren(SelectionFooter)
    assert len(footers) == 1
    return ctx, parent, footers[0]


def test_image_table_all_and_none_change_its_wells_and_its_count(image_table, qtbot):
    ctx, _parent, footer = image_table
    qtbot.mouseClick(footer.allButton(), Qt.LeftButton)
    assert ctx.state.image_table.active_wells == set(LOADED)
    assert footer.text() == "3 wells selected"
    qtbot.mouseClick(footer.noneButton(), Qt.LeftButton)
    assert ctx.state.image_table.active_wells == set()
    assert footer.text() == "No wells selected"


def test_image_table_puts_help_and_saved_selections_right_under_the_footer(image_table):
    _ctx, parent, footer = image_table
    items = _layout_widgets(parent)
    i = items.index(footer)
    assert isinstance(items[i + 1], QLabel) and "Generate" in items[i + 1].text(), (
        "the instruction is not directly under the footer")
    assert items[i + 2].objectName() == "PlotGroupsDrawer", (
        "Saved selections is not directly under the instruction")
    assert items[i + 3] == "stretch" and items[i + 3:] == ["stretch"], (
        "something sits between the picker and the rail's slack")


def test_the_saved_selections_header_counts_what_it_holds(image_table):
    _ctx, parent, _footer = image_table
    drawer = parent.findChild(QWidget, "PlotGroupsDrawer")
    assert drawer.titleText() == "Saved selections (2)"


# ── the plotting pages' main frame ──────────────────────────────────────────

@pytest.fixture
def main_frame(qtbot, monkeypatch):
    import review.core.selection_controller as sc
    from review.core.sidebar_view import build_sidebar

    calls = []
    monkeypatch.setattr(sc, "select_all", lambda c: calls.append(("all", c)))
    monkeypatch.setattr(sc, "select_none", lambda c: calls.append(("none", c)))

    win = SimpleNamespace()
    ctx = _image_table_ctx()
    ctx.widgets = WidgetRegistry(app=win)
    ctx.state.dataset.well_labels = {}
    win._ctx = ctx
    parent = QWidget()
    qtbot.addWidget(parent)
    build_sidebar(win, parent)
    return win, parent, calls


def test_the_main_footer_is_the_count_the_selection_controller_writes(main_frame):
    win, parent, _calls = main_frame
    (footer,) = parent.findChildren(SelectionFooter)
    # ``refresh_sidebar_map_now`` writes the count to ``app._sel_count_lbl``.
    assert win._sel_count_lbl is footer
    assert not parent.findChildren(QWidget, "DeselectAll")


def test_the_main_footer_buttons_select_all_and_none(main_frame, qtbot):
    win, parent, calls = main_frame
    (footer,) = parent.findChildren(SelectionFooter)
    qtbot.mouseClick(footer.allButton(), Qt.LeftButton)
    qtbot.mouseClick(footer.noneButton(), Qt.LeftButton)
    assert calls == [("all", win._ctx), ("none", win._ctx)]


def test_the_main_frame_puts_saved_selections_under_the_footer(main_frame):
    win, parent, _calls = main_frame
    items = _layout_widgets(parent)
    i = items.index(win._sel_count_lbl)
    assert items[i + 1] is win._line_group_hint
    assert items[i + 2].objectName() == "PlotGroupsDrawer", (
        "Saved selections is not directly under the footer and hint")
    assert items[-1] == "stretch" and items.count("stretch") == 1


def test_a_page_built_frame_is_built_into_the_main_frame_and_hidden(qtbot, monkeypatch):
    """A ``RailSection`` with ``build`` (Heat Map's configurator) is built by
    ``build_sidebar`` inside the main frame, under Saved selections and above
    the slack, registered under its key and hidden until its page shows it."""
    import review.core.selection_controller as sc
    from review.core.feature_registry import MAIN_RAIL_SECTION, RailSection
    from review.core.sidebar_view import build_sidebar

    monkeypatch.setattr(sc, "select_all", lambda c: None)
    monkeypatch.setattr(sc, "select_none", lambda c: None)
    made = []

    def _factory():
        def _build(ctx, parent):
            w = QWidget(parent)
            parent.layout().addWidget(w)
            made.append(w)
            return w
        return _build

    win = SimpleNamespace()
    ctx = _image_table_ctx()
    ctx.widgets = WidgetRegistry(app=win)
    ctx.state.dataset.well_labels = {}
    ctx.features.rail_sections = lambda: [MAIN_RAIL_SECTION,
                                          RailSection("_page_frame", build=_factory)]
    win._ctx = ctx
    parent = QWidget()
    qtbot.addWidget(parent)
    build_sidebar(win, parent)

    (frame,) = made
    assert ctx.widgets.get("_page_frame") is frame
    assert frame.isHidden()
    items = _layout_widgets(parent)
    assert items.index(frame) == items.index(
        parent.findChild(QWidget, "PlotGroupsDrawer")) + 1
    assert items[-1] == "stretch"
