"""The range page: what reaches the caller, and what never does."""

from __future__ import annotations

import pytest

from widgets.range_page import RangePage, format_number, parse_number


@pytest.fixture
def page(qtbot):
    w = RangePage()
    qtbot.addWidget(w)
    w.setRange(120.0, 3400.0)
    return w


def _type(edit, text: str) -> None:
    edit.setText(text)
    edit.editingFinished.emit()


def test_a_valid_range_reaches_the_caller_as_numbers(page):
    seen: list[tuple] = []
    page.changed.connect(lambda lo, hi: seen.append((lo, hi)))
    _type(page.high_edit, "2900")
    assert seen == [(120.0, 2900.0)]
    assert page.summary() == "120–2900"


@pytest.mark.parametrize("low,high", [("500", "400"), ("abc", "900"), ("", "900"), ("300", "300")])
def test_a_half_typed_or_inverted_range_applies_nothing(page, low, high):
    """A maximum at or under the minimum, a field that is not a number, one
    field empty: the picture is left alone rather than drawn in a range nobody
    asked for."""
    seen: list[tuple] = []
    page.changed.connect(lambda lo, hi: seen.append((lo, hi)))
    page.low_edit.setText(low)
    _type(page.high_edit, high)
    assert seen == []


def test_auto_and_both_fields_empty_are_auto(page):
    seen: list[tuple] = []
    page.changed.connect(lambda lo, hi: seen.append((lo, hi)))
    page.auto_button.click()
    assert seen == [(None, None)] and page.low_edit.text() == "" and page.summary() == "auto"
    _type(page.low_edit, "10")
    page.low_edit.setText("")
    _type(page.high_edit, "")
    assert seen[-1] == (None, None)


def test_reset_returns_to_the_range_the_page_opened_with_not_to_auto(page):
    """Opened on 120–3400, so Reset and Auto are different answers."""
    seen: list[tuple] = []
    page.changed.connect(lambda lo, hi: seen.append((lo, hi)))
    _type(page.low_edit, "900")
    page.reset_button.click()
    assert seen[-1] == (120.0, 3400.0)
    assert (page.low_edit.text(), page.high_edit.text()) == ("120", "3400")


def test_filling_the_page_emits_nothing(qtbot):
    w = RangePage()
    qtbot.addWidget(w)
    seen: list = []
    w.changed.connect(lambda lo, hi: seen.append((lo, hi)))
    w.setRange(1.0, 2.0)
    w.setCaption("GFP")
    assert seen == [] and w.caption.text() == "GFP" and not w.caption.isHidden()


def test_numbers_read_and_print():
    assert parse_number(" 12.5 ") == 12.5 and parse_number("x") is None
    assert format_number(None) == "" and format_number(3400.0) == "3400"
