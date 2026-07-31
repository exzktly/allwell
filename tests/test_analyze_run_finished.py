"""Pins the runFinished positive-latch rules on AnalyzeTab._poll_log.

A job queue's scheduler advances (or blocks) entirely on this signal, so the
rule that matters most is: success is a positive latch on having actually
seen a ``("done", …)`` event, never "the absence of an error". These tests
drive ``_poll_log`` directly via the tab's own log queue, offscreen and
without a real pipeline subprocess.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pytestqt.exceptions import capture_exceptions


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from analyze_tab import AnalyzeTab
    t = AnalyzeTab()
    try:
        yield t
    finally:
        # __init__ calls _poll_log(), which self-rearms a QTimer while
        # _alive — stop it so a later timer fire doesn't touch a
        # C++-deleted widget after this test's qapp cleans up.
        t._alive = False
        if t._runner.is_running:
            t._runner.stop()


def test_done_then_finished_emits_true(tab):
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append((ok, msg)))

    tab._log_q.put(("done", "Pipeline completed successfully.\n"))
    tab._log_q.put(("finished", None))
    tab._poll_log()

    assert seen == [(True, "")]


def test_bare_finished_with_no_done_emits_false(tab):
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append((ok, msg)))

    tab._log_q.put(("finished", None))
    tab._poll_log()

    assert len(seen) == 1
    ok, _msg = seen[0]
    assert ok is False


def test_error_then_finished_emits_false_with_message(tab):
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append((ok, msg)))

    tab._log_q.put(("error", "Pipeline exited with code 1.\n"))
    tab._log_q.put(("finished", None))
    tab._poll_log()

    assert len(seen) == 1
    ok, msg = seen[0]
    assert ok is False
    assert "exited with code 1" in msg


def test_first_error_message_wins_on_multiple_errors(tab):
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append((ok, msg)))

    tab._log_q.put(("error", "first failure\n"))
    tab._log_q.put(("error", "second failure\n"))
    tab._log_q.put(("finished", None))
    tab._poll_log()

    assert len(seen) == 1
    ok, msg = seen[0]
    assert ok is False
    assert "first failure" in msg
    assert "second failure" not in msg


def test_stop_requested_readable_as_cancel_not_failure(tab):
    """A user Stop makes the subprocess exit non-zero, which the latch
    reports as ok=False like any other failure — a job queue bridge
    distinguishes cancel from failure via PipelineRunner.was_stopped,
    which must still read True at the point runFinished fires."""
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append((ok, msg)))

    tab._runner._stop_requested.set()
    tab._log_q.put(("error", "Pipeline exited with code -15.\n"))
    tab._log_q.put(("finished", None))
    tab._poll_log()

    assert len(seen) == 1
    ok, _msg = seen[0]
    assert ok is False
    assert tab._runner.was_stopped is True


def test_latch_resets_between_runs(tab):
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append((ok, msg)))

    tab._log_q.put(("error", "boom\n"))
    tab._log_q.put(("finished", None))
    tab._poll_log()
    assert seen[-1][0] is False

    # start_run() resets the latch before the next run; poking the same
    # reset here keeps this test independent of AnalyzeRunMixin internals.
    tab._run_ok = False
    tab._run_error = None
    tab._log_q.put(("done", "Pipeline completed successfully.\n"))
    tab._log_q.put(("finished", None))
    tab._poll_log()
    assert seen[-1] == (True, "")


def test_runfinished_slot_exception_does_not_abort_drain(tab):
    """The emit is wrapped so a raising slot can't leave the drain (and
    the scheduler it feeds) half-advanced with no traceback. A raising
    Qt slot surfaces through Qt's own exception hook regardless of our
    try/except (PySide can't propagate it as a normal Python exception
    across the C++ emit boundary) — capture_exceptions lets the test
    assert on that without pytest-qt failing the test outright."""
    def _boom(_ok, _msg):
        raise RuntimeError("slot blew up")

    tab.runFinished.connect(_boom)
    tab._log_q.put(("done", "Pipeline completed successfully.\n"))
    tab._log_q.put(("finished", None))
    with capture_exceptions() as exceptions:
        tab._poll_log()  # must not raise past this call

    assert len(exceptions) == 1
    assert isinstance(exceptions[0][1], RuntimeError)
    assert tab._running is False
