"""A batch handed to the batch driver either reaches it, or the run says it did not.

The report: a batched nanowell run uploaded its fourth batch, logged
``batch 4: upload complete …; handing it to the driver`` and then said nothing
for an hour. The hand-off is one ``ssh touch <run>/.aw_batch_<i>_ready``, and it
ran with ``check=False`` and its result unread, so a hand-off that never reached
the remote left the app waiting on the driver and the driver polling — for up to
two hours — for a file that was never going to appear.

These tests drive the real batch loop (``_BatchedRemoteProcess._run_batches``)
against a fake driver stream, in the order the app runs it: upload, hand off,
wait for the driver's marker. See ``services/remote_handoff.py``.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

from services import remote_handoff
from services.remote_handoff import HandoffWatch
from tests.test_batch_driver import _FakeProc, _batched, _stub_runner


@pytest.fixture(autouse=True)
def _fast_handoff(monkeypatch):
    """No real pauses between attempts; a short acknowledgement window."""
    monkeypatch.setattr(remote_handoff, "FLAG_RETRY_S", 0.0)
    monkeypatch.setattr(remote_handoff, "ACK_TIMEOUT_S", 0.5)
    monkeypatch.setattr(remote_handoff, "FLAG_CONFIRM_S", 0.5)


def _ready(argv, index=None):
    """Whether *argv* is a ``touch`` of a ready flag (of batch *index*, when given)."""
    leaf = f".aw_batch_{index}_ready" if index is not None else "_ready"
    return argv[0] == "touch" and str(argv[-1]).endswith(leaf)


def _ended_as_a_failure_not_a_stop(p):
    """The run's own end, read after the reader has seen the stream close (`_FakeProc.terminate` closes it)."""
    if p._reader is not None:
        p._reader.join(timeout=10)
    assert not p._stop.is_set(), "a run the app gave up on was reported as the user's Stop"
    assert p._cancelled == [], "the downloads of the finished wells were cancelled"


def _run(tmp_path, monkeypatch, wells, ssh_answer):
    """Start the batch loop with *ssh_answer(argv) -> returncode* standing in for the host.

    Returns the process, the fake driver, the worker thread and every ssh argv asked.
    """
    proc = _FakeProc()
    p = _batched(tmp_path, wells)
    _stub_runner(p, proc, [])
    asked: list[list[str]] = []

    def ssh_run(argv, *, check, **kw):
        asked.append([str(a) for a in argv])
        return types.SimpleNamespace(stdout="", returncode=ssh_answer(argv))

    p._runner._cache._ssh_run = ssh_run
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    return p, proc, worker, asked


def _touches(asked, index):
    return [a for a in asked if a[0] == "touch" and a[-1].endswith(f".aw_batch_{index}_ready")]


def _until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the batch loop")
        time.sleep(0.02)


def test_a_touch_that_fails_is_tried_again_and_the_batch_still_runs(tmp_path, monkeypatch):
    fails = {"left": 2}

    def answer(argv):
        if argv[0] == "touch" and str(argv[-1]).endswith(".aw_batch_1_ready") and fails["left"]:
            fails["left"] -= 1
            return 255
        return 0

    p, proc, worker, asked = _run(tmp_path, monkeypatch, ["A01"], answer)
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    _until(lambda: len(_touches(asked, 1)) == 3)
    proc.say("[remote] batch-driver: batch 1 starting\n")
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert p._returncode == 0
    assert len(_touches(asked, 1)) == 3, "two failed attempts, then the one that took"
    assert "ERROR" not in "".join(p._logged)


def test_a_hand_off_the_remote_refuses_ends_the_run_and_says_so(tmp_path, monkeypatch):
    """``touch`` itself failing (exit 1) is proof the flag is not there: the run ends, as a failure, at once."""
    p, proc, worker, asked = _run(
        tmp_path, monkeypatch, ["A01", "A02"],
        lambda argv: 1 if _ready(argv) else 0)
    worker.join(timeout=20)

    assert not worker.is_alive(), "the loop must not wait on a batch the driver never got"
    assert p._returncode == 1
    log = "".join(p._logged)
    assert "ERROR batch 1/2 could not be handed to the driver" in log
    assert "the remote refused it" in log
    assert "Resume picks them up" not in log, "the message promises what a resume cannot always do"
    assert remote_handoff.KEPT in log
    assert p.failure_lines[0].startswith("[remote] ERROR batch 1/2 could not be handed to the driver")
    assert len(_touches(asked, 1)) == remote_handoff.FLAG_ATTEMPTS
    assert not _touches(asked, 2), "no further batch is handed to a driver that has not taken this one"
    assert proc.terminated, "the allocation nobody is feeding was kept"
    _ended_as_a_failure_not_a_stop(p)


def test_an_unconfirmed_hand_off_to_a_waiting_driver_that_never_starts_it_ends_the_run(tmp_path, monkeypatch):
    """ssh says nothing (255), and the driver — waiting for the batch — does not start it: the hand-off is lost."""
    p, proc, worker, asked = _run(
        tmp_path, monkeypatch, ["A01", "A02"],
        lambda argv: 255 if _ready(argv) else 0)
    proc.say("[remote] batch-driver: waiting for batch 1 of 2\n")
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert p._returncode == 1
    log = "".join(p._logged)
    assert "the connection to the host is failing" in log
    assert "ERROR batch 1/2 could not be handed to the driver" in log
    _ended_as_a_failure_not_a_stop(p)


def test_a_touch_that_timed_out_but_landed_is_not_given_up_on(tmp_path, monkeypatch):
    """Every ``touch`` times out (124), yet the driver says it started the batch: the hand-off was made."""
    def answer(argv):
        if _ready(argv, 1):
            proc_box[0].say("[remote] batch-driver: batch 1 starting\n")      # it landed before the timeout
            return 124
        return 0

    proc_box: list = []
    original = _FakeProc.__init__

    def init(self):
        original(self)
        proc_box.append(self)

    monkeypatch.setattr(_FakeProc, "__init__", init)
    p, proc, worker, asked = _run(tmp_path, monkeypatch, ["A01"], answer)
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    _until(lambda: watch_started(p, 1))
    proc.batch_finished(p, 1)
    worker.join(timeout=20)
    proc.finish()

    assert p._returncode == 0, "".join(p._logged)
    assert "ERROR" not in "".join(p._logged)
    assert not proc.terminated, "a batch the driver had started was killed"


def test_an_unconfirmed_touch_before_the_driver_asks_is_checked_when_it_does(tmp_path, monkeypatch):
    """No "waiting" line yet — the job is queued — so nobody can say whether the flag landed: the run waits, and the
    chase checks the flag once the driver asks for the batch."""
    state = {"flag": False}

    def answer(argv):
        if _ready(argv, 1):
            return 255
        if argv[0] == "test":
            return 0 if state["flag"] else 1
        return 0

    p, proc, worker, asked = _run(tmp_path, monkeypatch, ["A01"], answer)
    _until(lambda: "could not confirm" in "".join(p._logged))
    assert worker.is_alive(), "a hand-off that may have landed ended a run whose job is still queued"
    state["flag"] = True                    # it had landed
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    _until(lambda: "the node has it" in "".join(p._logged))
    proc.say("[remote] batch-driver: batch 1 starting\n")
    proc.batch_finished(p, 1)
    worker.join(timeout=20)
    proc.finish()

    assert p._returncode == 0
    assert "ERROR" not in "".join(p._logged)


def test_a_flag_check_that_could_not_run_is_not_read_as_missing(tmp_path, monkeypatch):
    """``test -f`` over a failing connection (255) says nothing about the flag: no re-raise, no give-up."""
    p, proc, worker, asked = _run(
        tmp_path, monkeypatch, ["A01"],
        lambda argv: 255 if argv[0] == "test" else 0)
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    _until(lambda: len([a for a in asked if a[0] == "test"]) >= 2)        # asked again, a window later
    proc.say("[remote] batch-driver: batch 1 starting\n")
    proc.batch_finished(p, 1)
    worker.join(timeout=20)
    proc.finish()

    assert p._returncode == 0
    log = "".join(p._logged)
    assert "did not answer whether its hand-off arrived" in log
    assert log.count("did not answer whether its hand-off arrived") == 1, "said once, not every window"
    assert "handing it over again" not in log
    assert "ERROR" not in log
    assert len(_touches(asked, 1)) == 1


def test_a_stop_during_the_retry_pause_ends_quietly_as_a_stop(tmp_path, monkeypatch):
    """Stop pressed between two attempts: the stop's own code, and no word of a failing connection."""
    monkeypatch.setattr(remote_handoff, "FLAG_RETRY_S", 30.0)
    p, proc, worker, asked = _run(
        tmp_path, monkeypatch, ["A01"],
        lambda argv: 1 if _ready(argv) else 0)
    _until(lambda: len(_touches(asked, 1)) == 1)
    p._stop.set()
    worker.join(timeout=10)
    proc.finish()

    assert not worker.is_alive(), "the retry pause did not see the Stop"
    assert p._returncode == 130
    log = "".join(p._logged)
    assert "ERROR" not in log, log
    assert "stopped while handing batch 1/1 to the driver" in log
    assert len(_touches(asked, 1)) == 1


def watch_started(p, index):
    """Whether the reader has seen the driver say it started batch *index*."""
    return index in remote_handoff.watch_of(p)._started


def test_a_lost_hand_off_is_raised_again_when_the_driver_waits_on_it(tmp_path, monkeypatch):
    """The touch said yes, the file is not there, and the driver says it is waiting."""
    p, proc, worker, asked = _run(
        tmp_path, monkeypatch, ["A01"],
        lambda argv: 1 if argv[0] == "test" else 0)
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    _until(lambda: len(_touches(asked, 1)) == 2)
    proc.say("[remote] batch-driver: batch 1 starting\n")
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)

    assert p._returncode == 0
    log = "".join(p._logged)
    assert "WARNING the driver has been waiting for batch 1/1" in log
    assert "handing it over again" in log
    assert [a for a in asked if a[0] == "test"] == [["test", "-f", _touches(asked, 1)[0][-1]]]


def test_a_driver_that_takes_neither_hand_off_is_given_up_on(tmp_path, monkeypatch):
    p, proc, worker, asked = _run(
        tmp_path, monkeypatch, ["A01", "A02"],
        lambda argv: 1 if argv[0] == "test" else 0)
    proc.say("[remote] batch-driver: waiting for batch 1 of 2\n")
    worker.join(timeout=20)
    proc.finish()

    assert not worker.is_alive()
    assert p._returncode == 1
    assert "ERROR the driver is waiting for batch 1/2 but did not start it" in "".join(p._logged)
    _ended_as_a_failure_not_a_stop(p)
    assert len(_touches(asked, 1)) == 2
    assert not _touches(asked, 2)


def test_a_driver_that_has_the_flag_but_goes_quiet_is_named_once_and_waited_for(tmp_path, monkeypatch):
    """The flag is there: the batch may be running, so the run waits, after saying so once."""
    p, proc, worker, asked = _run(tmp_path, monkeypatch, ["A01"], lambda argv: 0)
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    _until(lambda: "the node's output is not reaching the app" in "".join(p._logged))
    time.sleep(2.5)                                 # several more overdue checks
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)

    assert p._returncode == 0
    assert "".join(p._logged).count("the node's output is not reaching the app") == 1
    assert len(_touches(asked, 1)) == 1, "a flag that is there is not raised again"


def test_a_driver_still_queued_is_never_chased(tmp_path, monkeypatch):
    """No "waiting for batch" line yet: the allocation has not started, and silence is expected."""
    p, proc, worker, asked = _run(tmp_path, monkeypatch, ["A01"], lambda argv: 0)
    time.sleep(2.5)
    proc.say("[remote] batch-driver: waiting for batch 1 of 1\n")
    proc.say("[remote] batch-driver: batch 1 starting\n")
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)

    assert p._returncode == 0
    assert not [a for a in asked if a[0] == "test"]
    assert len(_touches(asked, 1)) == 1
    assert "WARNING" not in "".join(p._logged)


def test_the_clock_runs_from_the_later_of_handed_and_waiting():
    now = {"t": 0.0}
    watch = HandoffWatch(clock=lambda: now["t"])
    watch.handed(3)
    now["t"] = 1000.0
    assert not watch.overdue(3), "the driver has not said it is waiting: it may be queued or still busy"
    watch.observe("[remote] batch-driver: waiting for batch 3 of 4\n")
    now["t"] = 1000.0 + remote_handoff.ACK_TIMEOUT_S
    assert not watch.overdue(3)
    now["t"] += 1.0
    assert watch.overdue(3)
    watch.observe("[remote] batch-driver: batch 3 starting\n")
    assert not watch.overdue(3)
