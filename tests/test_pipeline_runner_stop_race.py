"""Audit E21 — stop() and the subprocess launch must not race.

``_spawn_tracked`` makes "check _stop_requested, then assign _proc" atomic
against stop()'s "set _stop_requested, then read _proc" under a shared lock.
Either stop() wins (the subprocess never launches) or the launch wins and
stop() sees the assigned handle and can signal it — never a launch that
orphans a subprocess because stop() read _proc as None.
"""
from __future__ import annotations


from services.pipeline_runner import PipelineRunner


class _FakeProc:
    def __init__(self):
        self.signalled = False

    def poll(self):
        return None


def test_spawn_tracked_skips_launch_when_already_stopped():
    r = PipelineRunner()
    r._stop_requested.set()
    called = {"n": 0}

    def _spawn():
        called["n"] += 1
        return _FakeProc()

    proc = r._spawn_tracked(_spawn)
    assert proc is None
    assert called["n"] == 0, "spawn_fn must not run once a stop is requested"
    assert r._proc is None


def test_spawn_tracked_launches_and_records_when_not_stopped():
    r = PipelineRunner()
    fake = _FakeProc()
    proc = r._spawn_tracked(lambda: fake)
    assert proc is fake
    assert r._proc is fake


def test_stop_before_launch_prevents_orphan():
    # Simulate the race deterministically: stop() runs to completion, then the
    # worker reaches its launch point. The launch must be suppressed.
    r = PipelineRunner()
    fake = r._proc = None

    launched = []

    def _spawn():
        p = _FakeProc()
        launched.append(p)
        return p

    # GUI thread stops first.
    r.stop()
    # Worker thread attempts the launch afterwards.
    proc = r._spawn_tracked(_spawn)
    assert proc is None
    assert launched == [], "a subprocess launched after stop() would be orphaned"


def test_stop_signals_a_launched_proc(monkeypatch):
    r = PipelineRunner()
    signalled = []
    monkeypatch.setattr(r, "_signal_subprocess", lambda p: signalled.append(p))
    fake = _FakeProc()
    r._spawn_tracked(lambda: fake)  # launch wins
    r.stop()
    assert fake in signalled, "stop() must signal the subprocess it can see"
