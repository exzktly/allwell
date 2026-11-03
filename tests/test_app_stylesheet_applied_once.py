"""The application stylesheet is applied only when its text changes.

``QApplication.setStyleSheet`` re-polishes every widget alive in the process.
The shell and Review each applied the same sheet while a window was built, and
in the slow lane — where a built app is never reclaimed — every build walked
every widget the earlier ones left: a fourth build spent 55 s of 65 s there,
over 13,788 widgets, and tests/test_home_page.py run whole timed out ten times
at pytest's 90 s limit. ``theme.apply_app_stylesheet`` skips identical text.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from ui import theme


@pytest.fixture
def calls(qapp, monkeypatch):
    seen = []
    real = QApplication.setStyleSheet
    monkeypatch.setattr(QApplication, "setStyleSheet",
                        lambda self, s: (seen.append(s), real(self, s))[1])
    before = qapp.styleSheet()
    yield seen
    real(qapp, before)


def test_the_same_sheet_is_not_applied_twice(qapp, calls):
    sheet = theme.qss()
    QApplication.setStyleSheet(qapp, "")          # a known starting point
    calls.clear()
    assert theme.apply_app_stylesheet(qapp, sheet) is True
    assert theme.apply_app_stylesheet(qapp, sheet) is False
    assert calls == [sheet]
    assert qapp.styleSheet() == sheet


def test_a_changed_sheet_is_applied(qapp, calls):
    QApplication.setStyleSheet(qapp, "QWidget { color: red; }")
    calls.clear()
    assert theme.apply_app_stylesheet(qapp, "QWidget { color: blue; }") is True
    assert calls == ["QWidget { color: blue; }"]


def test_building_the_shell_and_review_applies_the_sheet_at_most_once(qapp, calls):
    """The two places a window build applied the sheet go through the helper:
    with the sheet already in place, neither touches it."""
    import allwell
    from types import SimpleNamespace
    from review.core.ui_helpers import apply_theme
    QApplication.setStyleSheet(qapp, theme.qss())
    calls.clear()
    allwell.AllWellApp._apply_stylesheet(allwell.AllWellApp.__new__(allwell.AllWellApp))
    apply_theme(SimpleNamespace(window=None))
    assert calls == []
