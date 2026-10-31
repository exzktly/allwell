"""Both log views put a blank line between entries, and start every entry with the time it was logged.

Requested: "In app log, add a blank line between log entries; also make sure entries are time-stamped."

A record is often more than one line — a traceback, a multi-line summary — and with entries packed line against
line a traceback's last line read as the start of the next record. Both views are driven through their real
handlers with their real formatters (the ones ``_attach_log_ring_buffer`` and ``status_view.build_bottom``
install), and the text the widget ends up holding is read back exactly, so a view that spaces entries any other
way — no blank line, one before the first entry, the blank line lost inside an HTML entry — fails.
"""

from __future__ import annotations

import logging
import re

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QTextEdit  # noqa: E402

import allwell  # noqa: E402
from core import log_view  # noqa: E402
from review.core import status_view as SV  # noqa: E402

STAMP = re.compile(r"^\d\d:\d\d:\d\d  ")


class _OpenDrawer:
    def isVisible(self) -> bool:
        return True


def _records():
    """Two records, the first spanning several lines (a traceback), as the views receive them."""
    try:
        raise ValueError("the first record's traceback")
    except ValueError:
        import sys

        exc = sys.exc_info()
    first = logging.LogRecord("review.x", logging.ERROR, __file__, 1, "first record of <well>", None, exc)
    second = logging.LogRecord("review.y", logging.INFO, __file__, 2, "second record", None, None)
    return first, second


def _ring_handler_and_app():
    app = allwell.AllWellApp.__new__(allwell.AllWellApp)
    app._log_ring, app._log_ring_attached = [], False
    app._log_view, app._log_drawer = QTextEdit(), _OpenDrawer()
    app._attach_log_ring_buffer()
    handler = None
    for name in ("review", ""):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if type(h).__name__ == "_RingHandler":
                handler = h
                lg.removeHandler(h)
    assert handler is not None
    return handler, app


def test_the_app_log_drawer_spaces_its_entries_and_stamps_each(qtbot):
    handler, app = _ring_handler_and_app()
    first, second = _records()
    handler.handle(first)
    handler.handle(second)
    assert len(app._log_ring) == 2
    expected = app._log_ring[0] + "\n\n" + app._log_ring[1]
    qtbot.waitUntil(lambda: app._log_view.toPlainText() == expected, timeout=3000)
    assert "Traceback" in app._log_ring[0]                       # the multi-line entry is one entry
    assert all(STAMP.match(entry) for entry in app._log_ring)
    assert app._log_ring[1].endswith("INFO     review.y  second record")


def test_the_drawer_refilled_from_its_ring_is_spaced_the_same_way(qtbot):
    handler, app = _ring_handler_and_app()
    for record in _records():
        handler.handle(record)
    qtbot.waitUntil(lambda: "second record" in app._log_view.toPlainText(), timeout=3000)
    live = app._log_view.toPlainText()

    class _ShutDrawer:
        def isVisible(self):
            return False

        def open(self):
            pass

    app._log_drawer = _ShutDrawer()
    app._log_view.clear()
    app._toggle_log_drawer()                # opening the drawer refills it from the ring
    assert app._log_view.toPlainText() == live


def test_review_s_status_log_spaces_its_html_entries_and_stamps_each(qtbot):
    edit = QTextEdit()
    handler = SV._GUILogHandler(edit)
    handler.setFormatter(log_view.log_formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    first, second = _records()
    handler.handle(first)
    handler.handle(second)
    qtbot.waitUntil(lambda: "second record" in edit.toPlainText(), timeout=3000)
    entries = edit.toPlainText().split("\n\n")
    assert len(entries) == 2, edit.toPlainText()
    assert all(STAMP.match(entry) for entry in entries)
    # The whole entry as formatted, line breaks, spacing and ``<well>`` included: as plain HTML a traceback
    # collapsed onto one line and ``<well>`` was parsed as a tag and vanished.
    assert entries[0] == handler.format(first)
    assert "of <well>\nTraceback (most recent call last):" in entries[0]
    assert entries[1].endswith("INFO     second record")


def test_a_log_view_formatter_must_start_with_the_time():
    assert log_view.log_formatter("%(asctime)s %(message)s").datefmt == "%H:%M:%S"
    with pytest.raises(ValueError):
        log_view.log_formatter("%(levelname)s %(message)s")
