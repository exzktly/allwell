"""The single-launch remote process keeps its promises to the Fetcher.

Row R3 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) moves the
remote process wrappers out of ``services/remote_runner.py``. Characterised by
mutation first, against every test that reaches the remote client, and the
single-launch wrapper turned out to have no test at all: each of these survived
with the whole suite green —

* the tee stopped handing lines to the ``Fetcher`` (no per-well download would
  ever start, and the run would finish with nothing fetched until the
  end-of-run retry);
* ``wait`` stopped flushing the ``Fetcher`` (the run could be reported finished
  with downloads still in flight);
* ``terminate`` stopped cancelling it (Stop would leave an rsync running);
* ``wait`` stopped ending the heartbeat (a later session on the same plate would
  read a finished run as alive and refuse to sweep it).

A real child process stands in for the ssh, so the tee thread and the pipe are
the real ones; the ``Fetcher`` and heartbeat are recorders.

The batched driver's lost-node path is also here: ``_await_batch`` must mark a
run whose allocation vanished as failed, which nothing asserted.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from services import remote_process
from services.remote_run_state import LocalInputMode, _RemoteRun


class _Fetcher:
    def __init__(self) -> None:
        self.observed: list[str] = []
        self.flushed = 0
        self.cancelled = 0

    def observe(self, line: str) -> None:
        self.observed.append(line)

    def flush_and_join(self, timeout: float = 60.0) -> None:
        self.flushed += 1

    def cancel(self) -> None:
        self.cancelled += 1


class _Heartbeat:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


def _run(tmp_path: Path) -> _RemoteRun:
    return _RemoteRun(
        run_id="r", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=tmp_path, local_input_mode=LocalInputMode.ZIP)


def _child(code: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                            text=True)


def test_every_line_reaches_both_the_reader_and_the_fetcher(tmp_path):
    lines = ["planning\n", "Well A01 — done.\n", "Well B02 — done.\n"]
    child = _child("import sys; sys.stdout.write(%r)" % "".join(lines))
    fetcher = _Fetcher()
    proc = remote_process._RemoteProcess(child, _run(tmp_path), fetcher,
                                         heartbeat=_Heartbeat())
    read = list(proc.stdout)
    proc.wait()
    assert read == lines
    assert fetcher.observed == lines


def test_waiting_flushes_the_fetcher_and_ends_the_heartbeat(tmp_path):
    child = _child("print('Well A01 — done.')")
    fetcher, beat = _Fetcher(), _Heartbeat()
    proc = remote_process._RemoteProcess(child, _run(tmp_path), fetcher,
                                         heartbeat=beat)
    list(proc.stdout)
    assert proc.wait() == 0
    assert fetcher.flushed == 1
    assert beat.stopped == 1
    # A second exit path ends nothing twice.
    proc.kill()
    assert beat.stopped == 1


def test_stopping_cancels_the_downloads_and_the_heartbeat(tmp_path):
    child = _child("import time; print('started', flush=True); time.sleep(60)")
    fetcher, beat = _Fetcher(), _Heartbeat()
    proc = remote_process._RemoteProcess(child, _run(tmp_path), fetcher,
                                         heartbeat=beat)
    assert next(iter(proc.stdout)) == "started\n"
    proc.terminate()
    child.wait(timeout=10)
    assert fetcher.cancelled == 1
    assert beat.stopped == 1
    assert child.returncode is not None


def test_a_lost_driver_marks_the_run_failed(tmp_path):
    """No exit code came back for the batch and the reader saw the driver go
    away: the run is a failure, not a success with nothing to show."""
    p = object.__new__(remote_process._BatchedRemoteProcess)
    p._returncode = None
    p._batch_events = {}
    p._batch_rc = {}
    p._stop = threading.Event()
    p._stream_ended = threading.Event()
    p._driver_lost = threading.Event()
    logs: list[str] = []
    p._log = logs.append
    p._stream_ended.set()
    p._driver_lost.set()

    assert p._await_batch(2, 3) is None
    assert p._returncode == 1
    assert any("allocation ended during batch 2/3" in m for m in logs)


def test_a_stopped_batch_reports_the_interrupt_code(tmp_path):
    p = object.__new__(remote_process._BatchedRemoteProcess)
    p._returncode = None
    p._batch_events = {}
    p._batch_rc = {}
    p._stop = threading.Event()
    p._stream_ended = threading.Event()
    p._driver_lost = threading.Event()
    p._log = lambda _m: None
    p._stop.set()

    assert p._await_batch(1, 3) is None
    assert p._returncode == 130
