"""The heartbeat's watchdog: a beat that never returns, and a process that was not running, both say so.

A run on a granted node was lost with nothing in its log: the heartbeat stopped, the node's script released the node
ten minutes later, and Slurm cancelled the run's step. Either a beat call never returned — the miss counter that prints
the WARNING and ERROR lines is only reached when one does — or the whole app was not running. The log could not tell
those apart, so the cause could not be found. The watchdog makes each leave a line: a stuck beat names where its thread
is waiting; a pause shows as the watchdog's own wake-up arriving late.

Wrong implementations these reject: a watchdog never started by ``start()`` (the real path every run takes); one that
says nothing while a beat hangs inside the ssh call; one that repeats itself every poll; one that reports a pause for
an ordinary wake-up, or a stuck beat between beats.
"""

from __future__ import annotations

import threading
import time

import services.remote_heartbeat as hb
from services.remote_heartbeat import PAUSED_S, STUCK_BEAT_S, WATCH_POLL_S, _UploadHeartbeat


class _HangingCache:
    """A cache whose ssh call blocks until released, as a beat stuck in a hung ``subprocess`` call does."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def _ssh_run(self, argv, *, check, timeout_s):
        self.entered.set()
        self.release.wait(30)
        return type("R", (), {"returncode": 0, "stderr": ""})()


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_a_beat_that_never_returns_is_reported_once_with_where_it_waits():
    cache, mono, said = _HangingCache(), _Clock(), []
    h = _UploadHeartbeat(cache, "/stage/holds/x/busy", interval_s=0.01, log_fn=said.append, monotonic=mono)
    h.start()
    try:
        assert cache.entered.wait(5), "the beat thread never started a beat"
        mono.t += STUCK_BEAT_S - 1
        h._watch_once()
        assert not [m for m in said if "stuck" in m], "reported stuck before its limit"
        mono.t += 2
        h._watch_once()
        stuck = [m for m in said if "heartbeat is stuck" in m]
        assert len(stuck) == 1, said
        assert f"has not returned in {STUCK_BEAT_S + 1:.0f} s" in stuck[0], stuck[0]
        assert "in _ssh_run" in stuck[0] and "remote_heartbeat.py" in stuck[0], stuck[0]
        h._watch_once()
        assert len([m for m in said if "heartbeat is stuck" in m]) == 1, "the same stuck beat was reported twice"
    finally:
        cache.release.set()
        h.stop()


def test_a_late_wake_says_the_app_was_not_running_and_an_ordinary_one_says_nothing():
    clock, said = _Clock(), []
    h = _UploadHeartbeat(_HangingCache(), "/x/busy", log_fn=said.append, clock=clock)
    h._watched_at = clock.t
    clock.t += WATCH_POLL_S + 5                  # an ordinary, slightly late wake-up
    h._watch_once()
    assert said == []
    clock.t += WATCH_POLL_S + PAUSED_S + 600     # ten minutes and more with the process not running
    h._watch_once()
    assert len(said) == 1 and "this app was not running for about" in said[0], said
    assert f"about {PAUSED_S + 600:.0f} s" in said[0], said[0]


def test_nothing_is_reported_between_beats():
    """No beat in progress is not a stuck beat, however long since the last one."""
    mono, said = _Clock(), []
    h = _UploadHeartbeat(_HangingCache(), "/x/busy", log_fn=said.append, monotonic=mono)
    h._watched_at = h._clock()
    mono.t += 10 * STUCK_BEAT_S
    h._watch_once()
    assert said == []


def test_start_runs_the_watchdog_and_stop_ends_it(monkeypatch):
    """Through the path every run takes: `start()` / `stop()`, with the watchdog's own loop looking on its own."""
    monkeypatch.setattr(hb, "WATCH_POLL_S", 0.05)
    cache, said = _HangingCache(), []
    h = _UploadHeartbeat(cache, "/x/busy", interval_s=0.01, log_fn=said.append)
    monkeypatch.setattr(hb, "STUCK_BEAT_S", 0.2)
    h.start()
    try:
        assert cache.entered.wait(5)
        deadline = time.monotonic() + 5
        while not [m for m in said if "heartbeat is stuck" in m] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert [m for m in said if "heartbeat is stuck" in m], "the watchdog's own loop never reported the stuck beat"
        watcher = h._watcher
        assert watcher is not None and watcher.is_alive()
    finally:
        cache.release.set()
        h.stop(wait_s=5)
    assert not watcher.is_alive(), "stop() left the watchdog running"
