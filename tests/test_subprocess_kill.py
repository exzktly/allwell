"""Tests for services.subprocess_kill.signal_subprocess.

The implementation has POSIX and Windows branches gated by ``os.name``.
We mock the platform via ``signal_subprocess.__module__``'s ``os.name``
attribute to exercise both branches on a single test runner.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import services.subprocess_kill as sk
from services.subprocess_kill import signal_subprocess


def _make_proc(*, alive: bool = True, pid: int = 4242) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None if alive else 0
    return proc


def test_signal_subprocess_none_is_noop():
    # No exception, no work.
    signal_subprocess(None)


def test_signal_subprocess_dead_proc_is_noop():
    proc = _make_proc(alive=False)
    signal_subprocess(proc)
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


def test_signal_subprocess_posix_uses_killpg(monkeypatch):
    proc = _make_proc()
    fake_killpg = MagicMock()
    fake_getpgid = MagicMock(return_value=9999)
    fake_timer = MagicMock()
    fake_timer.return_value.start = MagicMock()
    monkeypatch.setattr(sk.os, "name", "posix")
    monkeypatch.setattr(sk.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(sk.os, "getpgid", fake_getpgid, raising=False)
    monkeypatch.setattr(sk.threading, "Timer", fake_timer)

    signal_subprocess(proc)

    fake_getpgid.assert_called_once_with(proc.pid)
    # SIGTERM dispatched to the pgid, not the bare pid.
    fake_killpg.assert_called_once()
    args, _ = fake_killpg.call_args
    assert args[0] == 9999
    # Escalation scheduled.
    fake_timer.assert_called_once()
    timer_args = fake_timer.call_args.args
    assert timer_args[0] == 5.0


def test_signal_subprocess_posix_falls_back_when_getpgid_fails(monkeypatch):
    """If getpgid raises (e.g. process already dead), terminate the
    plain process handle instead of crashing."""
    proc = _make_proc()
    monkeypatch.setattr(sk.os, "name", "posix")
    monkeypatch.setattr(
        sk.os, "getpgid",
        MagicMock(side_effect=ProcessLookupError()),
        raising=False,
    )
    monkeypatch.setattr(sk.threading, "Timer", MagicMock())
    signal_subprocess(proc)
    proc.terminate.assert_called_once()


def test_signal_subprocess_windows_uses_ctrl_break(monkeypatch):
    proc = _make_proc()
    monkeypatch.setattr(sk.os, "name", "nt")
    # signal.CTRL_BREAK_EVENT exists on Windows; on this Linux test
    # runner we provide it via monkeypatch.
    monkeypatch.setattr(sk.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(sk.threading, "Timer", MagicMock())
    signal_subprocess(proc)
    proc.send_signal.assert_called_once_with(1)


def test_signal_subprocess_poll_raising_does_not_propagate(monkeypatch):
    """A Popen-compatible remote wrapper whose ``poll()`` raises (e.g. a
    network blip) must not crash the GUI's Stop handler — an exception
    escaping the Qt slot aborts the whole app (PySide6 ≥ 6.5)."""
    proc = MagicMock()
    proc.poll.side_effect = RuntimeError("ssh channel closed")
    monkeypatch.setattr(sk.threading, "Timer", MagicMock())
    signal_subprocess(proc)  # must return cleanly
    proc.terminate.assert_not_called()


def test_signal_subprocess_non_int_pid_does_not_propagate(monkeypatch):
    """A wrapper whose ``pid`` is None makes ``os.getpgid(None)`` raise
    TypeError on POSIX — previously this escaped through stop() into the
    Stop slot and aborted the app. It must now fall back to terminate()."""
    proc = MagicMock()
    proc.pid = None
    proc.poll.return_value = None  # alive
    monkeypatch.setattr(sk.os, "name", "posix")
    monkeypatch.setattr(sk.threading, "Timer", MagicMock())
    signal_subprocess(proc)  # must not raise
    proc.terminate.assert_called_once()


def test_escalation_timer_skips_killpg_if_proc_exited(monkeypatch):
    """Captured pgid stays bound for the 5-second window. If the
    process exited cleanly in the meantime, the SIGKILL escalation
    must check ``proc.poll()`` again and skip — otherwise it could
    SIGKILL a PID that the OS recycled."""
    proc = _make_proc()
    captured_callback = {}

    def fake_timer_ctor(delay, fn):
        captured_callback["fn"] = fn
        return MagicMock(start=MagicMock())

    monkeypatch.setattr(sk.os, "name", "posix")
    monkeypatch.setattr(sk.os, "killpg", MagicMock(), raising=False)
    monkeypatch.setattr(sk.os, "getpgid", MagicMock(return_value=1111), raising=False)
    monkeypatch.setattr(sk.threading, "Timer", fake_timer_ctor)

    signal_subprocess(proc)

    # Now the process "exits cleanly".
    proc.poll.return_value = 0
    sk.os.killpg.reset_mock()  # type: ignore[attr-defined]
    # Trigger the escalation callback.
    captured_callback["fn"]()
    # No SIGKILL should have been issued.
    sk.os.killpg.assert_not_called()  # type: ignore[attr-defined]
