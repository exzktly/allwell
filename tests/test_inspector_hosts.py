"""Where a window's inspector is hosted: a column at the desk, the Controls
sheet in phone layout — and that the same calls reach either.

The window here is the minimal shell's (``tests/_minimal_shell.py``) with the
splitter Review's pages sit in built the way ``app_shell_view`` builds it: the
rail as pane 0 and the page as pane 1.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QSplitter, QVBoxLayout, QWidget

from review.core import inspector_service as svc
from tests._minimal_shell import minimal_shell


@pytest.fixture
def desk(qtbot, monkeypatch):
    ctx, window = minimal_shell(qtbot)
    split = QSplitter(Qt.Horizontal)
    rail = QWidget()
    rail.setFixedWidth(200)
    page = QWidget()
    lay = QVBoxLayout(page)
    labels = {n: QLabel(n, page) for n in ("a", "b", "c")}
    for lab in labels.values():
        lay.addWidget(lab)
    split.addWidget(rail)
    split.addWidget(page)
    window.setCentralWidget(split)
    window._h_pane = split
    window.resize(1280, 800)
    window.show()
    leaf = {"name": "Bar Plots"}
    monkeypatch.setattr(svc, "current_leaf", lambda _ctx: leaf["name"])
    return ctx, window, split, rail, page, labels, leaf


def _order(page) -> list[str]:
    lay = page.layout()
    return [lay.itemAt(i).widget().text() for i in range(lay.count())]


def test_at_the_desk_the_inspector_is_the_last_pane_and_the_rail_keeps_its_index(desk):
    ctx, _win, split, rail, page, labels, _leaf = desk
    assert svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    dock = svc.host(ctx).dock
    assert svc.is_open(ctx)
    assert split.indexOf(rail) == 0 and split.indexOf(page) == 1
    assert split.indexOf(dock) == 2
    assert dock.isAncestorOf(labels["b"])
    assert svc.inspector(ctx).currentKey() == "fc"
    svc.close(ctx)
    assert split.count() == 2 and split.indexOf(rail) == 0
    assert _order(page) == ["a", "b", "c"]
    assert not svc.is_open(ctx)


def test_the_column_takes_its_width_from_the_page_and_never_from_the_rail(desk):
    ctx, _win, split, rail, page, labels, _leaf = desk
    before = page.width()
    svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    dock = svc.host(ctx).dock
    QApplication.processEvents()
    assert rail.width() == 200
    assert svc.MIN_WIDTH <= dock.width() <= svc.MAX_WIDTH
    assert page.width() < before


def test_the_close_button_closes_the_column_and_gives_the_page_back(desk):
    ctx, _win, split, _rail, page, labels, _leaf = desk
    svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    svc.inspector(ctx).close_button.click()
    assert not svc.is_open(ctx)
    assert _order(page) == ["a", "b", "c"]


def test_back_from_a_data_page_at_the_desk_closes_when_nothing_else_is_there(desk):
    """At the desk the Data tab has no first page (it is the band), so Back
    from a Data page with no Display or Style to fall to closes the column."""
    ctx, _win, _split, _rail, page, labels, _leaf = desk
    svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    svc.inspector(ctx).back_button.click()
    assert not svc.is_open(ctx)
    assert _order(page) == ["a", "b", "c"]


def test_a_page_change_gives_the_last_page_its_widgets_and_fills_from_the_new_one(desk):
    ctx, _win, _split, _rail, page, labels, leaf = desk
    other = QLabel("line display")
    other.setParent(page)
    roots = {"Bar Plots": labels["a"], "Line Graphs": other}
    finder = lambda _ctx, name: {"display": roots.get(name)}   # noqa: E731
    svc.add_root_finder(finder)
    try:
        assert svc.open_tab(ctx, "display")
        insp = svc.inspector(ctx)
        assert insp.title() == "Bar Plots" and insp.tabWidget("display") is labels["a"]
        svc.push(ctx, "fc", "Fold change", labels["b"], tab="display")
        leaf["name"] = "Line Graphs"
        svc.sync(ctx)
        assert _order(page) == ["a", "b", "c"]            # both went home
        assert svc.is_open(ctx)                           # the column stays
        assert insp.title() == "Line Graphs" and insp.tabWidget("display") is other
        assert insp.depth() == 0
        leaf["name"] = "Home"                             # a page with nothing to show
        svc.sync(ctx)
        assert not svc.is_open(ctx)
        leaf["name"] = "Bar Plots"                        # and it returns with the next that has
        svc.sync(ctx)
        assert svc.is_open(ctx) and insp.tabWidget("display") is labels["a"]
    finally:
        svc._root_finders.remove(finder)


def test_the_fixed_button_toggles_its_tab(desk):
    ctx, _win, _split, _rail, page, labels, _leaf = desk
    finder = lambda _ctx, _name: {"display": labels["a"], "style": labels["c"]}   # noqa: E731
    svc.add_root_finder(finder)
    try:
        assert svc.toggle_tab(ctx, "display") is True
        assert svc.inspector(ctx).currentTab() == "display"
        assert svc.toggle_tab(ctx, "style") is True        # another tab: switches, stays open
        assert svc.is_open(ctx) and svc.inspector(ctx).currentTab() == "style"
        assert svc.toggle_tab(ctx, "style") is False       # the same tab: closes
        assert not svc.is_open(ctx)
        assert _order(page) == ["a", "b", "c"]
    finally:
        svc._root_finders.remove(finder)


def test_a_tab_the_page_has_nothing_for_does_not_open(desk):
    ctx, *_ = desk
    assert svc.open_tab(ctx, "style") is False
    assert not svc.is_open(ctx)


def test_detail_shows_the_picked_thing_and_clearing_it_closes_an_empty_inspector(desk):
    ctx, _win, _split, _rail, page, labels, _leaf = desk
    assert svc.show_detail(ctx, labels["c"], "Cell 14")
    insp = svc.inspector(ctx)
    assert insp.currentTab() == "detail" and insp.currentWidget() is labels["c"]
    svc.clear_detail(ctx)
    assert not svc.is_open(ctx)
    assert _order(page) == ["a", "b", "c"]


def test_the_width_the_user_left_is_remembered(desk):
    from core import app_settings as settings
    ctx, _win, split, _rail, _page, labels, _leaf = desk
    svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    dock = svc.host(ctx).dock
    sizes = split.sizes()
    sizes[1] -= 60
    sizes[2] += 60
    split.setSizes(sizes)
    split.splitterMoved.emit(0, 2)
    assert int(settings.app_settings().value(svc.WIDTH_SETTING)) == dock.width()
    want = dock.width()
    svc.close(ctx)
    svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    assert svc.host(ctx).dock.width() == want


def test_switching_layout_gives_a_pushed_page_back_first(desk):
    from core import phone_layout
    ctx, _win, split, _rail, page, labels, _leaf = desk
    svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    for listener in list(phone_layout._listeners):
        if getattr(listener, "__self__", None) is svc.host(ctx):
            listener(True)
    assert _order(page) == ["a", "b", "c"]
    assert split.count() == 2


def test_in_phone_layout_the_same_calls_reach_the_registered_phone_host(desk, monkeypatch):
    from core import phone_layout
    ctx, _win, split, _rail, _page, labels, _leaf = desk

    class Phone:
        open_ = False

        def open_inspector(self):
            self.open_ = True
            return True

        def close_inspector(self):
            self.open_ = False

        def inspector_is_open(self):
            return self.open_

    phone = Phone()
    svc.set_phone_host(ctx, phone)
    monkeypatch.setattr(phone_layout, "is_on", lambda: True)
    assert svc.push(ctx, "fc", "Fold change", labels["b"], tab="data")
    assert phone.open_ and svc.is_open(ctx)
    assert split.count() == 2                       # no desk column in phone layout
    assert svc.inspector(ctx).currentKey() == "fc"
    svc.close(ctx)
    assert not phone.open_


def test_in_split_view_a_control_pressed_in_the_other_pane_makes_that_pane_the_one_served(desk):
    """A button press does not move Split View's focus (a button is not pane
    content), so the service focuses the pane the pressed control sits in."""
    ctx, _win, _split, _rail, page, labels, _leaf = desk
    pane_a, pane_b = QWidget(page), QWidget(page)
    in_b = QLabel("in b", pane_b)
    split = ctx.runtime.split
    split.active = True
    split.pane_widgets = {"Plotting": pane_a, "Image Browser": pane_b}
    split.focused = "Plotting"
    try:
        svc.push(ctx, "x", "X", labels["b"], tab="data", anchor=in_b)
        assert split.focused == "Image Browser"
    finally:
        split.active = False
        split.pane_widgets = {}
