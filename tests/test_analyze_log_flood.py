"""A talkative run must not take the window with it.

Reported: during a remote run, while the dashboard sat on *Grouped — pipeline
(segmentation + zipping)*, the "Show raw log" checkbox could not be ticked.

Nothing was wrong with the checkbox. ``_poll_log`` drained its queue with an
unbounded ``while True`` on the GUI thread, and every line went into the
``QTextEdit`` through its own insert — which reads ``verticalScrollBar()
.maximum()`` either side, forcing a full re-layout of a ``NoWrap`` document, so
the cost of a line grew with the document beneath it:

    500 lines into an empty log   3.4 ms each
    1500 lines                    9.5 ms each
    3000 lines                   19.1 ms each  → 57 s for the batch

Segmentation and zipping is exactly where a remote run says the most, so the
window stopped answering anything for as long as the flood lasted. The queue is
capped at ``_LOG_QUEUE_MAXSIZE`` for the same reason — its comment already
anticipated "a stalled UI thread".

These assert the two properties that keep the window alive, not the timings,
which belong to a machine rather than to the code.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    try:
        yield t
    finally:
        t._alive = False


def _flood(tab, n):
    for i in range(n):
        tab._log_q.put(("line", f"[remote] processing well A01 field {i} of 16\n"))


def test_one_poll_takes_a_slice_not_the_whole_queue(tab):
    """The property the window's responsiveness rests on: a poll returns to the
    event loop after a bounded amount of work, however much is waiting."""
    from analyze.tab import _POLL_MAX_LINES

    queued = _POLL_MAX_LINES * 4
    _flood(tab, queued)

    tab._poll_log()

    left = tab._log_q.qsize()
    assert left > 0, "one poll drained the whole queue — the GUI thread is held"
    drained = queued - left
    assert drained <= _POLL_MAX_LINES


def test_repeated_polls_drain_everything_in_order(tab):
    """Bounded, not lossy. The slices have to add up to the whole run, in the
    order it was said — a log that drops or reorders under load is worse than a
    slow one."""
    from analyze.tab import _POLL_MAX_LINES

    queued = _POLL_MAX_LINES * 3 + 7
    _flood(tab, queued)

    for _ in range(200):
        tab._poll_log()
        if tab._log_q.empty():
            break
    assert tab._log_q.empty()

    text = tab._log.toPlainText()
    assert "field 0 of 16" in text
    assert f"field {queued - 1} of 16" in text
    assert text.index("field 0 of 16") < text.index(f"field {queued - 1} of 16")


def test_a_run_s_last_words_land_below_the_output_they_summarise(tab):
    """Lines are buffered within a slice and written once; the ending is
    written straight through. So the buffer has to go down first, or the
    summary appears above the work it describes."""
    _flood(tab, 5)
    tab._log_q.put(("done", "pipeline finished\n"))

    for _ in range(20):
        tab._poll_log()
        if tab._log_q.empty():
            break

    text = tab._log.toPlainText()
    assert "field 4 of 16" in text and "pipeline finished" in text
    assert text.index("field 4 of 16") < text.index("pipeline finished")


def test_many_lines_are_written_with_few_inserts(tab):
    """Where the cost was. One insert per line is what made a line's price grow
    with the document under it; consecutive lines of one colour now go in
    together."""
    from PySide6.QtGui import QTextCursor

    inserts = []
    original = QTextCursor.insertText

    def spy(cursor, *args):
        inserts.append(args[0])
        return original(cursor, *args)

    QTextCursor.insertText = spy
    try:
        tab._log_lines([(f"line {i}\n", "INFO") for i in range(200)])
    finally:
        QTextCursor.insertText = original

    assert len(inserts) == 1, f"{len(inserts)} inserts for 200 same-colour lines"
    assert inserts[0].count("\n") == 200


def test_a_colour_change_still_starts_a_new_insert(tab):
    """Batching must not repaint a warning in the colour of the line above it."""
    from PySide6.QtGui import QTextCursor

    inserts = []
    original = QTextCursor.insertText

    def spy(cursor, *args):
        inserts.append(args[0])
        return original(cursor, *args)

    QTextCursor.insertText = spy
    try:
        tab._log_lines([("a\n", "INFO"), ("b\n", "INFO"),
                        ("bad\n", "ERROR"),
                        ("c\n", "INFO")])
    finally:
        QTextCursor.insertText = original

    assert [t for t in inserts] == ["a\nb\n", "bad\n", "c\n"]
