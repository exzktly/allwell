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


def test_rail_denominators_are_per_stage_not_one_shared_total(qtbot):
    """Regression guard for a rail that read "Group 40/6" and "Download 34/6".

    A remote run's stages do not count the same population. Every well on the
    plate is grouped locally; only the subset the incremental split decides to
    send is uploaded, processed and thresholded. One shared denominator meant
    the numerators counted the plate while the divisor counted the run — and
    Download, which includes the wells that were already current locally, came
    out ahead of an Upload that hadn't started."""
    from widgets.run_dashboard import RunDashboard

    plate = [f"{r}{c:02d}" for r in "ABCDE" for c in range(1, 9)]   # 40 wells
    uploaded, already_local = plate[:6], plate[6:]                  # 6 + 34

    dash = RunDashboard()
    qtbot.addWidget(dash)
    dash.reset(remote=True, total=len(uploaded))
    for well in plate:
        dash.set_well_stage(well, "group")
    for well in already_local:
        dash.set_well_stage(well, "download")

    def counts():
        return {k: dash._rail[k][3].text() for k in
                ("group", "upload", "process", "threshold", "download")}

    assert counts()["group"] == "40/6"        # the reported state
    assert counts()["download"] == "34/6"

    for stage in ("group", "download"):
        dash.set_stage_total(stage, len(plate))

    assert counts() == {
        "group": "40/40", "upload": "0/6", "process": "0/6",
        "threshold": "0/6", "download": "34/40",
    }
    # An unknown stage name is ignored rather than inventing a rail row.
    dash.set_stage_total("nonsense", 99)
    assert counts()["group"] == "40/40"
    # reset() drops the overrides with everything else.
    dash.reset(remote=True, total=7)
    for well in plate[:3]:
        dash.set_well_stage(well, "group")
    assert counts()["group"] == "3/7"
