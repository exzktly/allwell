"""The multi-select component: what reaches the caller, and what it calls itself."""

from __future__ import annotations

import pytest

from widgets.multi_select import MultiSelect, summarise


@pytest.fixture
def picker(qtbot):
    w = MultiSelect()
    qtbot.addWidget(w)
    # Keys that are not their labels, in an order that is not sorted, so that
    # "the labels" and "sorted" are both wrong answers for what is emitted.
    w.setItems([(24.0, "24 h"), (0.0, "0 h"), (12.0, "12 h"), (6.0, "6 h")], checked=[0.0, 6.0])
    return w


def test_a_tick_reaches_the_caller_at_once_with_the_keys_in_list_order(picker):
    seen: list[list] = []
    picker.changed.connect(seen.append)
    picker.boxes()[2].click()                      # "12 h"
    assert seen == [[0.0, 12.0, 6.0]], "not the keys, not in the list's order, or not on the tick"
    picker.boxes()[1].click()                      # untick "0 h"
    assert seen[-1] == [12.0, 6.0]


def test_filling_the_list_and_following_the_state_emit_nothing(picker):
    seen: list[list] = []
    picker.changed.connect(seen.append)
    picker.setItems(["a", "b", "c"], checked=["b"])
    picker.setChecked(["a", "c"])
    assert seen == []
    assert picker.checked() == ["a", "c"]


def test_all_and_none_emit_once_however_many_rows(picker):
    seen: list[list] = []
    picker.changed.connect(seen.append)
    picker.all_button.click()
    assert seen == [[24.0, 0.0, 12.0, 6.0]]
    picker.none_button.click()
    assert seen == [[24.0, 0.0, 12.0, 6.0], []]
    picker.none_button.click()                     # already none: nothing changed
    assert len(seen) == 2


def test_the_summary_is_all_none_or_a_count(picker):
    assert picker.summary() == "2 of 4"
    picker.setAll(True)
    assert picker.summary() == "All"
    picker.setAll(False)
    assert picker.summary() == "None"
    picker.setItems([])
    assert picker.summary() == "None"
    assert not picker.all_button.isEnabled()


@pytest.mark.parametrize("checked,total,expect", [
    (0, 0, "None"), (0, 5, "None"), (5, 5, "All"), (3, 8, "3 of 8"), (9, 8, "All"),
])
def test_summarise(checked, total, expect):
    assert summarise(checked, total) == expect


# ── a multi-select shown as an inspector page (review/core/inspector_pages) ───

@pytest.fixture
def page_ctx(qtbot):
    """A real context with the splitter the desk inspector docks in, and a
    button to open a page from."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton, QSplitter, QVBoxLayout, QWidget

    from tests._minimal_shell import minimal_shell
    ctx, window = minimal_shell(qtbot)
    split = QSplitter(Qt.Horizontal)
    split.addWidget(QWidget())
    page = QWidget()
    QVBoxLayout(page)
    button = QPushButton("Timepoints", page)
    page.layout().addWidget(button)
    split.addWidget(page)
    window.setCentralWidget(split)
    window._h_pane = split
    window.resize(1200, 700)
    window.show()
    return ctx, button


def test_a_settled_page_applies_once_after_the_burst_with_the_last_choice(page_ctx, qtbot):
    """An expensive application runs once per burst of ticks, with what the
    boxes read at the end — not once per tick, and not with the first tick."""
    from review.core import inspector_pages
    ctx, button = page_ctx
    applied: list[list] = []
    picker = inspector_pages.open_multi_select(
        ctx, button, key="t", title="Timepoints", items=["0", "6", "24"], checked=["0", "6", "24"],
        on_change=applied.append, settle_ms=60)
    picker.boxes()[0].click()
    picker.boxes()[1].click()
    picker.boxes()[1].click()
    assert applied == [], "a tick applied before the burst had settled"
    qtbot.waitUntil(lambda: bool(applied), timeout=3000)
    assert applied == [["6", "24"]]


def test_leaving_a_settled_page_applies_a_pending_choice_at_once(page_ctx, qtbot):
    """A choice still waiting out its pause when the page is left is applied
    then, not lost — there is no close every route out passes through."""
    from review.core import inspector_pages, inspector_service
    ctx, button = page_ctx
    applied: list[list] = []
    picker = inspector_pages.open_multi_select(
        ctx, button, key="t", title="Timepoints", items=["0", "6"], checked=["0", "6"],
        on_change=applied.append, settle_ms=60_000)        # would not fire in this test's life
    picker.none_button.click()
    assert applied == []
    inspector_service.close(ctx)
    assert applied == [[]]


def test_an_unsettled_page_applies_on_the_tick(page_ctx):
    from review.core import inspector_pages
    ctx, button = page_ctx
    applied: list[list] = []
    picker = inspector_pages.open_multi_select(
        ctx, button, key="t", title="Timepoints", items=["0", "6"], checked=["0"],
        on_change=applied.append)
    picker.boxes()[1].click()
    assert applied == [["0", "6"]]


# ── MultiSelectButton: the same component for a form ─────────────────────────

def _button(qtbot, labels=("a", "b", "c"), checked=()):
    from widgets.multi_select import MultiSelectButton

    button = MultiSelectButton(noun="things", empty_text="No things", title="Things")
    qtbot.addWidget(button)
    for label in labels:
        button.addItem(label.upper(), label)
    button.blockSignals(True)
    button.setCheckedData(list(checked))
    button.blockSignals(False)
    fired = []
    button.changed.connect(lambda: fired.append(button.checkedData()))
    return button, fired


def test_the_button_reads_the_choice_and_ends_in_a_chevron_not_a_menu_arrow(qtbot):
    button, _fired = _button(qtbot)
    assert button._button.text() == "No things ›"
    button.setCheckedData(["b"])
    assert button._button.text() == "1 of 3 things ›"
    button.setCheckedData(["a", "b", "c"])
    assert button._button.text() == "All 3 things ›"
    assert button._button.menu() is None


def test_pressing_it_opens_the_list_in_a_window_and_a_tick_is_one_change(qtbot):
    button, fired = _button(qtbot, checked=("a",))
    button._button.click()
    page = button.page()
    assert page is not None and page.window() is not button.window()
    assert page.window().windowTitle() == "Things" and page.window().isVisible()
    assert [b.text() for b in page.boxes()] == ["A", "B", "C"]
    assert [b.isChecked() for b in page.boxes()] == [True, False, False]
    page.boxes()[2].setChecked(True)
    assert fired == [["a", "c"]] and button._button.text() == "2 of 3 things ›"
    page.all_button.click()
    assert fired == [["a", "c"], ["a", "b", "c"]], "All is one change, not one per row"
    page.none_button.click()
    assert fired[-1] == [] and len(fired) == 3


def test_a_change_made_elsewhere_shows_on_the_open_page_and_a_refill_replaces_its_rows(qtbot):
    button, fired = _button(qtbot)
    button._button.click()
    page = button.page()
    button.setCheckedData(["b"])
    assert [b.isChecked() for b in page.boxes()] == [False, True, False]
    assert fired == [["b"]]
    button.blockSignals(True)
    button.clear()
    button.addItem("X", "x")
    button.addItem("Y", "y")
    button.setCheckedData(["y"])
    button.blockSignals(False)
    assert [b.text() for b in page.boxes()] == ["X", "Y"]
    assert [b.isChecked() for b in page.boxes()] == [False, True]
    assert fired == [["b"]], "refilling the list is not the user choosing"


def test_with_an_opener_the_list_is_shown_through_it_and_no_window_is_built(qtbot):
    button, fired = _button(qtbot, checked=("a",))
    handed = []
    button.setOpener(handed.append)
    button._button.click()
    assert handed == [button] and button.page() is None
    assert button.items() == [("a", "A"), ("b", "B"), ("c", "C")] and button.title() == "Things"
    button.applyChecked(["c", "b"])
    assert fired == [["b", "c"]], "the page's ticks are one change, in the list's own order"


def test_all_and_none_go_through_the_button_so_a_subclass_can_give_them_a_meaning(qtbot):
    """The converters' timepoint picker reads All as "every timepoint of the
    source", whatever is listed. The page's All must reach that override."""
    from widgets.multi_select import MultiSelectButton

    calls = []

    class Picker(MultiSelectButton):
        def _set_all(self, value: bool) -> None:
            calls.append(value)

        def _bulk_enabled(self) -> bool:
            return True

    picker = Picker(noun="things")
    qtbot.addWidget(picker)
    picker._button.click()                       # an empty list: All is still offered
    page = picker.page()
    assert page.all_button.isEnabled() and page.none_button.isEnabled()
    page.all_button.click()
    page.none_button.click()
    assert calls == [True, False]


def test_none_and_all_leave_a_disabled_row_as_it_is(qtbot):
    """A disabled row is an option that is always on (the ranked timepoint):
    None must not untick it on the page while the figure still shows it."""
    page = MultiSelect()
    qtbot.addWidget(page)
    page.setItems(["a", "b", "c"], ["a", "b", "c"])
    page.boxes()[1].setEnabled(False)
    seen = []
    page.changed.connect(seen.append)
    page.none_button.click()
    assert page.checked() == ["b"] and seen == [["b"]]
    page.all_button.click()
    assert page.checked() == ["a", "b", "c"]
