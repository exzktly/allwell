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
