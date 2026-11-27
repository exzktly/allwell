"""The inspector widget: tabs, pages, the header, and the lending of widgets.

Every lending test puts the lent widget *between* two siblings, so "appended at
the end" and "returned to its index" are different outcomes (CLAUDE.md §7.8 #2).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from widgets import form_registry
from widgets.inspector import FORM_KIND, Inspector


def _home(qtbot, names=("a", "b", "c", "d")):
    """A parent whose layout holds one label per name, in order."""
    home = QWidget()
    qtbot.addWidget(home)
    lay = QVBoxLayout(home)
    labels = {}
    for n in names:
        labels[n] = QLabel(n, home)
        lay.addWidget(labels[n])
    return home, lay, labels


def _order(lay) -> list[str]:
    return [lay.itemAt(i).widget().text() for i in range(lay.count())
            if lay.itemAt(i).widget() is not None]


@pytest.fixture
def insp(qtbot):
    w = Inspector()
    qtbot.addWidget(w)
    w.setTabs([("data", "Data"), ("display", "Display"), ("style", "Style")])
    return w


def test_a_pushed_widget_is_inside_the_inspector_and_visible(insp, qtbot):
    home, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    insp.show()
    insp.pushPage("fc", "Fold change", labels["c"])
    assert insp.isAncestorOf(labels["c"])
    assert labels["c"].isVisible()
    assert insp.currentKey() == "fc"
    assert insp.depth() == 1


def test_popping_gives_the_widget_back_at_its_index_with_its_width_limits(insp, qtbot):
    home, lay, labels = _home(qtbot)
    labels["b"].setMinimumWidth(37)
    labels["b"].setMaximumWidth(211)
    insp.setTabWidget("data", labels["d"])
    insp.pushPage("p", "P", labels["b"])
    assert (labels["b"].minimumWidth(), labels["b"].maximumWidth()) != (37, 211)
    assert insp.popPage() is True
    assert labels["b"].parentWidget() is home
    assert _order(lay)[:3] == ["a", "b", "c"]
    assert (labels["b"].minimumWidth(), labels["b"].maximumWidth()) == (37, 211)
    assert insp.depth() == 0


def test_two_widgets_from_one_layout_return_to_their_places_in_either_order(insp, qtbot):
    home, lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["b"])          # lent first, index 1
    insp.pushPage("p", "P", labels["c"])            # then index 2, read as 1
    insp.setTabWidget("data", None)                 # the first goes back first
    assert _order(lay) == ["a", "b", "d"]
    insp.popPage()
    assert _order(lay) == ["a", "b", "c", "d"]


def test_close_gives_back_every_page_of_every_tab(insp, qtbot):
    home, lay, labels = _home(qtbot)
    free = QLabel("free", home)                     # in no layout, like a popover's content
    insp.setTabWidget("data", labels["a"])
    insp.setTabWidget("display", labels["c"])
    insp.pushPage("p", "P", labels["b"], tab="data")
    insp.pushPage("q", "Q", free, tab="display")
    insp.giveBackAll()
    assert _order(lay) == ["a", "b", "c", "d"]
    assert free.parentWidget() is home and free.isHidden()
    assert not any(insp.holds(w) for w in (*labels.values(), free))
    assert insp.depth() == 0


def test_a_hidden_widget_goes_back_hidden(insp, qtbot):
    _home_, _lay, labels = _home(qtbot)
    labels["b"].hide()
    insp.setTabWidget("data", labels["a"])
    insp.pushPage("p", "P", labels["b"])
    assert not labels["b"].isHidden()
    insp.popPage()
    assert labels["b"].isHidden()


def test_back_is_hidden_on_a_first_page_and_shown_on_a_pushed_one(insp, qtbot):
    _h, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    insp.setTitle("Bar Plots")
    insp.show()
    assert not insp.back_button.isVisible()
    assert insp.title_label.text() == "Bar Plots"
    insp.pushPage("fc", "Fold change", labels["b"])
    insp.pushPage("pair", "Per-sample control", labels["c"])
    assert insp.back_button.isVisible()
    assert insp.title_label.text() == "Per-sample control"
    assert insp.crumb_label.text() == "Data › Fold change"
    insp.back_button.click()
    assert insp.currentKey() == "fc"
    insp.back_button.click()
    assert not insp.back_button.isVisible()
    assert insp.popPage() is False


def test_a_tab_with_no_widget_or_disabled_cannot_be_shown(insp, qtbot):
    _h, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    assert insp.showTab("style") is False           # no widget
    assert insp.currentTab() == "data"
    insp.setTabWidget("style", labels["b"])
    insp.setTabEnabled("style", False)
    assert insp.showTab("style") is False           # disabled
    assert insp.currentTab() == "data"
    insp.setTabEnabled("style", True)
    assert insp.showTab("style") is True
    assert insp.currentTab() == "style"
    assert insp.currentWidget() is labels["b"]


def test_a_large_page_asks_for_the_full_height_and_leaving_it_for_half(insp, qtbot):
    _h, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    sizes: list[str] = []
    insp.sizeWanted.connect(sizes.append)
    insp.pushPage("p", "P", labels["b"], large=True)
    assert sizes[-1] == "full"
    insp.popPage()
    assert sizes[-1] == "half"
    insp.pushPage("q", "Q", labels["c"])
    assert sizes[-1] == "half"


def test_close_asks_the_host_and_does_nothing_itself(insp, qtbot):
    _h, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    insp.pushPage("p", "P", labels["b"])
    asked: list[bool] = []
    insp.closeRequested.connect(lambda: asked.append(True))
    insp.close_button.click()
    assert asked == [True]
    assert insp.holds(labels["b"]) and insp.currentKey() == "p"
    assert insp.close_button.text() == "Close"


def test_page_changed_names_the_page_on_show(insp, qtbot):
    _h, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    insp.setTabWidget("display", labels["d"])
    seen: list[str] = []
    insp.pageChanged.connect(seen.append)
    insp.pushPage("fc", "Fold change", labels["b"], tab="display")
    assert seen[-1] == "fc" and insp.currentTab() == "display"
    insp.popPage()
    assert seen[-1] == "display"
    insp.showTab("data")
    assert seen[-1] == "data"


def test_pushing_a_page_already_on_the_stack_returns_to_it(insp, qtbot):
    _h, lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    insp.pushPage("fc", "Fold change", labels["b"])
    insp.pushPage("pair", "Per-sample", labels["c"])
    insp.pushPage("fc", "Fold change", labels["b"])
    assert insp.pageKeys() == ["fc"]
    assert not insp.holds(labels["c"])


def test_a_lent_widget_deleted_by_its_owner_leaves_no_page_behind(insp, qtbot):
    import shiboken6
    _h, _lay, labels = _home(qtbot)
    insp.setTabWidget("data", labels["a"])
    insp.pushPage("p", "P", labels["b"])
    shiboken6.delete(labels["b"])
    assert insp.depth() == 0
    assert insp.currentKey() == "data"
    assert insp.currentWidget() is labels["a"]


def test_deleting_the_inspector_gives_its_pages_back(qtbot):
    from PySide6.QtCore import QCoreApplication, QEvent
    home, lay, labels = _home(qtbot)
    insp = Inspector()
    insp.setTabs([("data", "Data")])
    insp.setTabWidget("data", labels["a"])
    insp.pushPage("p", "P", labels["c"])
    insp.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert _order(lay) == ["a", "b", "c", "d"]
    assert labels["c"].parentWidget() is home


def test_it_declares_itself_to_the_form_registry(insp):
    assert form_registry.kind_of(insp) == FORM_KIND
    assert insp in form_registry.declared(FORM_KIND)


def test_a_filled_tab_widget_gets_exactly_the_tab_s_height_even_when_it_wraps(insp, qtbot):
    """A widget lent with ``fill`` (a figure, which has no height of its own to
    ask for) takes its tab's height. One that holds a wrapping row has a height
    for its width, and a scroll area gives such content the sum of everything's
    *preferred* height instead: the Nanowells site detail was 1,547 px in an
    820 px tab, scrolled, its crops under the fold. The tall child here prefers
    1,500 px and can live in 40."""
    from PySide6.QtCore import QSize

    from widgets.flow_layout import FlowLayout

    class Tall(QWidget):
        def sizeHint(self):
            return QSize(200, 1500)

        def minimumSizeHint(self):
            return QSize(40, 40)

    home = QWidget()
    qtbot.addWidget(home)
    home_lay = QVBoxLayout(home)
    panel = QWidget(home)
    lay = QVBoxLayout(panel)
    row = QWidget(panel)
    flow = FlowLayout(row)
    for text in ("one", "two", "three"):
        flow.addWidget(QLabel(text))
    lay.addWidget(row)
    tall = Tall(panel)
    lay.addWidget(tall, 1)
    home_lay.addWidget(panel)
    assert lay.hasHeightForWidth(), "premise: the panel's layout wraps"

    insp.resize(320, 600)
    insp.show()
    qtbot.waitExposed(insp)
    insp.setTabWidget("style", panel, fill=True)
    insp.showTab("style")
    slot = insp.slotOf(panel)
    assert slot is not None
    qtbot.waitUntil(lambda: panel.height() > 100 and slot.verticalScrollBar().maximum() == 0, timeout=2000)
    assert panel.height() <= slot.viewport().height()
    assert panel.height() >= slot.viewport().height() - 40, "it has the tab's height, not a strip of it"
    assert tall.height() > 100, "the stretching child was given the room"

    # Given back, it is in its own layout again and nothing of the box is left.
    insp.setTabWidget("style", None)
    assert panel.parentWidget() is home and home_lay.indexOf(panel) == 0
    from PySide6.QtCore import QCoreApplication, QEvent

    from widgets.inspector import _FillBox
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not slot.widget().findChildren(_FillBox), "an empty box was left in the tab for every loan"
