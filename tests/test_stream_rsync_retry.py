"""Audit A11 — the shared streaming-rsync retry loop.

``ensure_uploaded`` and ``upload_subset`` hand-rolled the same
``_run_rsync_streaming`` + bounded-retry loop; both now call
``_stream_rsync_with_retry``. These pin its retry / accumulation / stop
behaviour (the streaming twin of the already-tested ``_rsync_with_retry``).
"""
from __future__ import annotations

import threading

import services.remote_runner as rr


def _patch_stream(monkeypatch, results):
    """Make _run_rsync_streaming yield (bytes, rc) from *results* in order."""
    seq = iter(results)
    calls = {"n": 0}

    def _fake(cmd, *, log_fn=None, progress_fn=None, stop_event=None):
        calls["n"] += 1
        return next(seq)

    monkeypatch.setattr(rr, "_run_rsync_streaming", _fake)
    monkeypatch.setattr(rr.time, "sleep", lambda _s: None)
    return calls


def test_first_try_success_no_retry(monkeypatch):
    calls = _patch_stream(monkeypatch, [(100, 0)])
    sent, elapsed, rc, attempts = rr._stream_rsync_with_retry(["rsync"])
    assert (sent, rc, attempts) == (100, 0, 1)
    assert calls["n"] == 1
    assert elapsed > 0


def test_transient_code_retries_and_accumulates_bytes(monkeypatch):
    # A transient code (10) retries; --partial means each attempt sends more.
    code = next(iter(rr._RSYNC_RETRY_CODES))
    calls = _patch_stream(monkeypatch, [(40, code), (60, 0)])
    sent, _elapsed, rc, attempts = rr._stream_rsync_with_retry(["rsync"])
    assert rc == 0
    assert sent == 100          # 40 (partial) + 60 (resumed)
    assert attempts == 2
    assert calls["n"] == 2


def test_non_transient_code_does_not_retry(monkeypatch):
    calls = _patch_stream(monkeypatch, [(10, 23)])  # 23 = partial-transfer, non-retry
    _sent, _elapsed, rc, attempts = rr._stream_rsync_with_retry(["rsync"])
    assert rc == 23
    assert attempts == 1
    assert calls["n"] == 1


def test_stop_event_set_breaks_immediately(monkeypatch):
    calls = _patch_stream(monkeypatch, [(0, 0)])
    ev = threading.Event()
    ev.set()
    sent, _elapsed, rc, attempts = rr._stream_rsync_with_retry(["rsync"], stop_event=ev)
    # Loop breaks at the top before any streaming call.
    assert attempts == 0
    assert rc == -1
    assert sent == 0
    assert calls["n"] == 0
