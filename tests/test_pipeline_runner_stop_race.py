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
    monkeypatch.setattr(r, "_signal_subprocess",
                        lambda p, **kw: signalled.append((p, kw)))
    fake = _FakeProc()
    r._spawn_tracked(lambda: fake)  # launch wins
    r.stop()
    assert (fake, {"escalate": True}) in signalled, (
        "stop() must signal the subprocess it can see, escalating to SIGKILL")


def test_suspend_signals_without_the_sigkill_escalation(monkeypatch):
    """A cooperative wind-down must outlive the five-second escalation.

    Suspend sends the same SIGTERM as Stop; the pipeline catches it and finishes
    the FOV-group it is on, which can easily take longer than five seconds. The
    SIGKILL that follows a Stop would destroy exactly the work suspend exists to
    preserve, so it is suppressed — the user's own second click is the fallback.
    """
    r = PipelineRunner()
    signalled = []
    monkeypatch.setattr(r, "_signal_subprocess",
                        lambda p, **kw: signalled.append((p, kw)))
    fake = _FakeProc()
    r._spawn_tracked(lambda: fake)
    r.suspend()
    assert (fake, {"escalate": False}) in signalled
    assert r.was_suspended is True
    assert r.was_stopped is True          # suspend is a stop, of a gentler kind
    assert r._cleanup_remote == "none", (
        "a suspended run's remote output is what a resume reads")
