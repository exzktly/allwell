"""The log drawer's handler must not feed its own failures back into the logger.

``_GUILogHandler`` writes records into a ``QTextEdit``. The handler outlives that
widget — nothing detaches it on teardown — so in a process that builds more than
one window (every full-app test file does) the dead handlers stay subscribed to
the ``review`` logger.

Logging a failure from inside a log handler is what made that fatal rather than
merely wasteful: the record propagates back to the logger the handler is attached
to, fails in the same place, and logs again. A redraw's ``processEvents()`` drains
that queue forever, which hung a slow-lane shard for ~30 minutes until CI killed
it. These tests pin both halves — no re-logging, and self-detach — plus the
document cap that keeps a long session's drawer bounded.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QTextEdit

from review.core import status_view as SV


class _DeadWidget:
    """Stands in for a QTextEdit whose C++ half has been deleted.

    Carries a ``destroyed`` signal because every widget does and the handler
    connects to it; these tests are about the other route, a record that
    arrives after the widget is gone, so the signal never fires here.
    """

    class _Signal:
        def connect(self, _slot) -> None:
            return None

    destroyed = _Signal()

    def append(self, _html):
        raise RuntimeError("Internal C++ object (QTextEdit) already deleted.")


class _CountingHandler(logging.Handler):
    """Counts records reaching the logger, so re-entrant logging is visible."""

    def __init__(self):
        super().__init__()
        self.count = 0

    def emit(self, record):
        self.count += 1


@pytest.fixture
def wv_logger():
    """The real ``review`` logger, restored afterwards."""
    logger = logging.getLogger("review")
    before = list(logger.handlers)
    level, propagate = logger.level, logger.propagate
    try:
        yield logger
    finally:
        logger.handlers = before
        logger.setLevel(level)
        logger.propagate = propagate


def test_append_on_a_dead_widget_logs_nothing(wv_logger):
    """The regression: a failed append must not emit a record.

    If it does, that record comes straight back to this handler, fails again, and
    the loop never terminates.
    """
    wv_logger.setLevel(logging.DEBUG)
    counter = _CountingHandler()
    counter.setLevel(logging.DEBUG)
    wv_logger.addHandler(counter)

    handler = SV._GUILogHandler(_DeadWidget())
    wv_logger.addHandler(handler)

    handler._append("<span>anything</span>")

    assert counter.count == 0, (
        "the handler logged its own failure — that record re-enters this handler "
        "and loops"
    )


def test_a_dead_handler_detaches_itself(wv_logger):
    """It can never write again, so it must stop receiving records."""
    handler = SV._GUILogHandler(_DeadWidget())
    wv_logger.addHandler(handler)
    assert handler in wv_logger.handlers

    handler._append("<span>anything</span>")

    assert handler not in wv_logger.handlers


def test_detach_is_idempotent_and_survives_a_second_append(wv_logger):
    # A queued singleShot can fire after the handler already detached; that must
    # not raise out of the Qt event loop.
    handler = SV._GUILogHandler(_DeadWidget())
    wv_logger.addHandler(handler)
    handler._append("first")
    handler._append("second")
    assert handler not in wv_logger.handlers


def test_handlers_do_not_accumulate_across_dead_widgets(wv_logger):
    """The leak itself: stacked dead handlers must not survive a round of records.

    Stands in for a process that builds several windows — each adds a handler and
    none is removed on teardown.
    """
    wv_logger.setLevel(logging.DEBUG)
    for _ in range(5):
        wv_logger.addHandler(SV._GUILogHandler(_DeadWidget()))
    assert sum(isinstance(h, SV._GUILogHandler) for h in wv_logger.handlers) == 5

    for h in list(wv_logger.handlers):
        if isinstance(h, SV._GUILogHandler):
            h._append("<span>x</span>")

    assert not any(isinstance(h, SV._GUILogHandler) for h in wv_logger.handlers)


def test_a_live_widget_still_receives_its_line(qapp, wv_logger):
    # The opt-out must be narrow: a working handler keeps working.
    edit = QTextEdit()
    handler = SV._GUILogHandler(edit)
    wv_logger.addHandler(handler)

    handler._append("<span>hello</span>")

    assert "hello" in edit.toPlainText()
    assert handler in wv_logger.handlers


def test_log_document_is_capped(qapp):
    """A long session must not grow the drawer without bound."""
    edit = QTextEdit()
    edit.document().setMaximumBlockCount(SV._LOG_MAX_BLOCKS)
    handler = SV._GUILogHandler(edit)
    for i in range(SV._LOG_MAX_BLOCKS + 250):
        handler._append(f"line {i}")

    assert edit.document().blockCount() <= SV._LOG_MAX_BLOCKS
    # It is a tail, not a head: the newest line survives, the oldest is gone.
    text = edit.toPlainText()
    assert f"line {SV._LOG_MAX_BLOCKS + 249}" in text
    assert "line 0\n" not in text
