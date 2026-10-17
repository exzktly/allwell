"""The application log's "!" badge: raised by a line the user has not seen.

The status bar's log button carries a :class:`widgets.notice_badge.NoticeBadge`.
A line that arrives while the log drawer is shut raises it; opening the drawer
clears it; a line that arrives while the drawer is open — and so is on screen —
does not. The tone escalates with the worst level seen since the last open, so
an error is not repainted back to the routine colour by the next INFO line.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QTextEdit, QToolButton

import allwell
from widgets.notice_badge import NoticeBadge


def _line(level: str, message: str = "something happened") -> str:
    # The drawer handler's own format: time, level, logger, message.
    return f"12:00:00  {level:<7s}  review  {message}"


class _Drawer:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    def isVisible(self) -> bool:
        return self.visible

    def open(self) -> None:
        self.visible = True

    def close(self) -> None:
        self.visible = False


def _shell(qapp, *, drawer_visible: bool):
    app = allwell.AllWellApp.__new__(allwell.AllWellApp)
    app._log_ring = []
    app._log_ring_attached = True          # no handler on the real loggers
    app._log_view = QTextEdit()
    app._log_drawer = _Drawer(drawer_visible)
    app._keep_btn = QToolButton()
    app._log_badge = NoticeBadge(app._keep_btn)
    return app


def test_badge_escalates_and_never_steps_back_down(qapp):
    host = QToolButton()
    badge = NoticeBadge(host)
    assert badge.tone() is None and not badge.isVisibleTo(host)

    badge.notify("accent")
    assert badge.tone() == "accent" and badge.isVisibleTo(host)
    badge.notify("danger")
    assert badge.tone() == "danger"
    badge.notify("warn")
    badge.notify("accent")
    assert badge.tone() == "danger", "an INFO after an ERROR must not hide the error"

    badge.clear()
    assert badge.tone() is None and not badge.isVisibleTo(host)
    badge.notify("warn")
    assert badge.tone() == "warn", "clear() forgets the previous tone"


def test_badge_lets_clicks_through_to_its_host(qapp):
    from PySide6.QtCore import Qt
    host = QToolButton()
    badge = NoticeBadge(host)
    assert badge.testAttribute(Qt.WA_TransparentForMouseEvents)


@pytest.mark.parametrize("level, tone", [
    ("INFO", "accent"), ("DEBUG", "accent"),
    ("WARNING", "warn"), ("ERROR", "danger"), ("CRITICAL", "danger"),
])
def test_a_line_with_the_drawer_shut_raises_the_badge(qapp, level, tone):
    app = _shell(qapp, drawer_visible=False)
    app._append_log_line(_line(level))
    assert app._log_badge.tone() == tone


def test_the_level_is_read_from_its_field_not_from_the_message(qapp):
    app = _shell(qapp, drawer_visible=False)
    app._append_log_line(_line("INFO", "wells failed : 0 — no ERROR here"))
    assert app._log_badge.tone() == "accent"


def test_a_never_opened_drawer_still_raises_the_badge(qapp):
    app = _shell(qapp, drawer_visible=False)
    app._log_drawer = None
    app._append_log_line(_line("WARNING"))
    assert app._log_badge.tone() == "warn"


def test_a_line_shown_in_the_open_drawer_raises_nothing(qapp):
    app = _shell(qapp, drawer_visible=True)
    app._append_log_line(_line("ERROR"))
    assert app._log_badge.tone() is None
    assert "something happened" in app._log_view.toPlainText()


def test_opening_the_drawer_clears_the_badge_and_closing_it_does_not(qapp):
    app = _shell(qapp, drawer_visible=False)
    app._append_log_line(_line("ERROR"))
    assert app._log_badge.tone() == "danger"

    app._toggle_log_drawer()               # opens
    assert app._log_drawer.isVisible()
    assert app._log_badge.tone() is None

    app._toggle_log_drawer()               # closes
    app._append_log_line(_line("INFO"))
    assert app._log_badge.tone() == "accent"
