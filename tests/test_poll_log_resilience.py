"""The Analyze live-log poller must survive a bad queue event.

``AnalyzeTab._poll_log`` re-arms itself on an 80 ms ``QTimer.singleShot``. If
handling a single dequeued event raised, the old code let the exception escape
before the re-arm — so the poller died and the live log + run dashboard went
permanently silent (while the rest of the GUI stayed responsive, since the Qt
event loop kept running). This pins the hardening: an error while handling an
event is caught and the timer is always re-armed, so the next tick continues.
"""
from __future__ import annotations

import os
import queue
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import analyze_tab as at
from analyze_tab import AnalyzeTab


def _fake_tab(q: queue.Queue, *, parse):
    fake = types.SimpleNamespace()
    fake._log_q = q
    fake._progress_tracker = types.SimpleNamespace(parse=parse)
    fake._log_line = lambda *a, **k: None
    fake._poll_log = lambda: None          # referenced by the re-arm call
    return fake


def test_poll_log_rearms_after_handler_exception(monkeypatch):
    rearms: list[int] = []
    monkeypatch.setattr(at.QTimer, "singleShot",
                        lambda _ms, _fn: rearms.append(1))

    q: queue.Queue = queue.Queue()
    q.put(("line", "[remote] ↓ A01: malformed drain line\n"))

    def _boom(_payload):
        raise RuntimeError("parser blew up on a drain-phase line")

    # Must NOT raise out of _poll_log, and must still re-arm the timer.
    AnalyzeTab._poll_log(_fake_tab(q, parse=_boom))
    assert rearms == [1]


def test_poll_log_rearms_on_clean_drain(monkeypatch):
    rearms: list[int] = []
    monkeypatch.setattr(at.QTimer, "singleShot",
                        lambda _ms, _fn: rearms.append(1))

    AnalyzeTab._poll_log(_fake_tab(queue.Queue(), parse=lambda _p: []))
    assert rearms == [1]
