"""Audit X10 / E20 — batched-remote teardown safety.

* X10: _run_batches must enqueue the stdout sentinel even if flush_and_join
  raises, or the reader loop in pipeline_runner blocks forever.
* E20: terminate()/kill()/pid snapshot _current_proc under a lock so a Stop
  racing the per-batch reassignment reads a consistent handle.
"""
from __future__ import annotations

import queue
import threading
import types

import pytest

import services.remote_runner as rr


def _bare_batched():
    p = object.__new__(rr._BatchedRemoteProcess)
    p._returncode = None
    p._current_proc_lock = threading.Lock()
    p._stop = threading.Event()
    return p


# ── X10 ─────────────────────────────────────────────────────────────────
def test_sentinel_enqueued_even_if_flush_raises():
    p = _bare_batched()

    class _BadZipped:
        @property
        def well_zips(self):
            raise RuntimeError("boom")  # crash the batch body

    def _flush_boom():
        raise RuntimeError("flush boom")

    p._zipped = _BadZipped()
    p._fetcher = types.SimpleNamespace(flush_and_join=_flush_boom)
    p._line_q = queue.Queue()
    p._log = lambda _m: None

    # flush failure still propagates out of the finally, but the sentinel must
    # have been enqueued first so the consumer's line iterator can terminate.
    with pytest.raises(RuntimeError):
        p._run_batches()
    assert p._line_q.get_nowait() is None


# ── E20 ─────────────────────────────────────────────────────────────────
def test_terminate_snapshots_and_terminates_current_proc():
    p = _bare_batched()
    calls = []
    p._current_proc = types.SimpleNamespace(terminate=lambda: calls.append("term"))
    p._fetcher = types.SimpleNamespace(cancel=lambda: calls.append("cancel"))
    p.terminate()
    assert p._stop.is_set()
    assert calls == ["term", "cancel"]


def test_pid_is_safe_when_no_current_proc():
    p = _bare_batched()
    p._current_proc = None
    assert p.pid == -1
