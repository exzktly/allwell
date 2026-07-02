"""signal_subprocess must never SIGTERM/SIGKILL the GUI's own process group.

A subprocess not started in a new session shares our process group; os.killpg
on it would take down the GUI ("Stop quit the whole app"). The helper must fall
back to terminating just that one process instead.
"""
from __future__ import annotations

import os

import pytest

from services import subprocess_kill as sk


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.killed = False
    def poll(self):
        return None          # still running
    def terminate(self):
        self.terminated = True
    def kill(self):
        self.killed = True


@pytest.mark.skipif(os.name != "posix", reason="pgid logic is POSIX-only")
def test_does_not_killpg_own_group(monkeypatch):
    our_group = 4242
    monkeypatch.setattr(os, "getpgrp", lambda: our_group)
    # The subprocess is in OUR group (no new session).
    monkeypatch.setattr(os, "getpgid", lambda _pid: our_group)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    proc = _FakeProc(pid=99999)
    sk.signal_subprocess(proc)

    assert killpg_calls == [], "must not signal our own process group"
    assert proc.terminated, "should fall back to terminating the single process"


@pytest.mark.skipif(os.name != "posix", reason="pgid logic is POSIX-only")
def test_killpg_used_for_separate_group(monkeypatch):
    monkeypatch.setattr(os, "getpgrp", lambda: 4242)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)   # own-session leader
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    proc = _FakeProc(pid=5555)
    sk.signal_subprocess(proc)

    assert killpg_calls and killpg_calls[0][0] == 5555, "should killpg the separate group"
    assert not proc.terminated
