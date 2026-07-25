"""Audit E1 — a crashed batched remote run must NOT report success.

``_BatchedRemoteProcess._run_batches`` ran in a daemon thread with a bare
``try/finally`` (no ``except``); an exception left ``_returncode = None`` and
``wait()`` coerced ``None -> 0``, so the CLI saw exit 0 and
``verify_and_cleanup(ok=True)`` deleted remote data though nothing ran.

These tests pin the two invariants of the fix:
  * ``wait()`` never turns a finished-but-uncoded worker into success, and
  * ``_run_batches`` records a non-zero code (and enqueues the stdout sentinel)
    when its body raises, instead of propagating and leaving ``None``.
Also covers audit G1 (``import sys`` present for ``_run_local_auto_threshold``).
"""
from __future__ import annotations

import queue
import types

import services.remote_runner as rr


def _bare_batched() -> rr._BatchedRemoteProcess:
    # Bypass the heavy __init__ (needs ssh config, staging, a Fetcher, …); we
    # only exercise wait()/_run_batches, so set just the attributes they touch.
    p = object.__new__(rr._BatchedRemoteProcess)
    p._returncode = None
    return p


class _DeadWorker:
    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def test_wait_none_after_finish_is_failure():
    p = _bare_batched()
    p._worker = _DeadWorker()
    p._returncode = None
    assert p.wait() == 1          # NOT 0
    assert p._returncode == 1


def test_wait_preserves_real_nonzero_code():
    p = _bare_batched()
    p._worker = _DeadWorker()
    p._returncode = 3
    assert p.wait() == 3


def test_wait_success_is_zero():
    p = _bare_batched()
    p._worker = _DeadWorker()
    p._returncode = 0
    assert p.wait() == 0


def test_run_batches_records_failure_on_crash():
    p = _bare_batched()

    class _BadZipped:
        @property
        def well_zips(self):
            raise RuntimeError("boom")  # first line of the batch loop

    p._zipped = _BadZipped()
    p._fetcher = types.SimpleNamespace(flush_and_join=lambda: None)
    p._line_q = queue.Queue()
    p._log = lambda _m: None

    # Must not propagate — the crash is caught, recorded, and surfaced.
    p._run_batches()

    assert p._returncode == 1
    # The stdout sentinel is still enqueued so the consumer's line iterator
    # terminates instead of blocking forever.
    assert p._line_q.get_nowait() is None


def test_sys_is_imported_for_local_auto_threshold():
    # G1: _run_local_auto_threshold() calls sys.path.insert(...); the module
    # must actually import sys (it previously did not → NameError, swallowed).
    import sys as _sys

    assert rr.sys is _sys
