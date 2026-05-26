"""Tests for the v5.1.1 rsync retry helper.

Covers the bounded-retry behaviour, the exit-code gating that
distinguishes transient network failures from logic errors, the
exponential-backoff schedule (verified via a mocked ``time.sleep``),
and the OSError fallthrough that synthesises a CompletedProcess so
callers see a uniform return type.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from services.remote_runner import (
    _RSYNC_RETRY_CODES,
    _RSYNC_RETRY_DELAYS,
    _rsync_with_retry,
)


def _ok(stdout: str = "ok") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        ["rsync"], returncode=0, stdout=stdout, stderr="",
    )


def _fail(code: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        ["rsync"], returncode=code, stdout="", stderr=stderr,
    )


def test_first_try_success_returns_immediately(monkeypatch):
    fake_run = MagicMock(return_value=_ok())
    fake_sleep = MagicMock()
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", fake_sleep)

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert result.returncode == 0
    assert fake_run.call_count == 1
    fake_sleep.assert_not_called()


def test_non_transient_exit_code_does_not_retry(monkeypatch):
    """Permission-denied (rsync exit 23) is a config error — retrying
    is just noise. Must return immediately."""
    fake_run = MagicMock(return_value=_fail(23, "permission denied"))
    fake_sleep = MagicMock()
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", fake_sleep)

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert result.returncode == 23
    assert fake_run.call_count == 1
    fake_sleep.assert_not_called()


@pytest.mark.parametrize("code", sorted(_RSYNC_RETRY_CODES))
def test_transient_exit_code_triggers_retries(code, monkeypatch):
    """Every retry-eligible code keeps trying up to the budget."""
    fake_run = MagicMock(return_value=_fail(code))
    fake_sleep = MagicMock()
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", fake_sleep)

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert result.returncode == code
    # 1 initial + 3 retries = 4 attempts.
    assert fake_run.call_count == 1 + len(_RSYNC_RETRY_DELAYS)
    # Slept exactly once between each retry pair.
    assert fake_sleep.call_count == len(_RSYNC_RETRY_DELAYS)


def test_retry_then_succeed(monkeypatch):
    """A transient blip followed by recovery should succeed after one
    or two retries without exhausting the budget."""
    fake_run = MagicMock(side_effect=[_fail(10), _fail(10), _ok()])
    fake_sleep = MagicMock()
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", fake_sleep)

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert result.returncode == 0
    assert fake_run.call_count == 3
    assert fake_sleep.call_count == 2


def test_backoff_schedule_is_exponential(monkeypatch):
    """Sleeps before retries 1/2/3 must match _RSYNC_RETRY_DELAYS."""
    fake_run = MagicMock(return_value=_fail(10))
    sleeps: list[float] = []
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr(
        "services.remote_runner.time.sleep", lambda s: sleeps.append(s),
    )

    _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert tuple(sleeps) == _RSYNC_RETRY_DELAYS


def test_oserror_in_subprocess_keeps_retrying(monkeypatch):
    """If the subprocess module itself raises (e.g. ENOENT for rsync
    binary not on PATH), we still retry per policy and synthesise a
    CompletedProcess so the caller sees a uniform return type."""
    fake_run = MagicMock(side_effect=OSError("connection reset"))
    fake_sleep = MagicMock()
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", fake_sleep)

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    # Synthesised non-zero exit with the OSError message in stderr.
    assert result.returncode != 0
    assert "connection reset" in result.stderr
    # 1 + 3 attempts.
    assert fake_run.call_count == 1 + len(_RSYNC_RETRY_DELAYS)


def test_timeout_in_subprocess_keeps_retrying(monkeypatch):
    fake_run = MagicMock(
        side_effect=subprocess.TimeoutExpired(cmd="rsync", timeout=60),
    )
    fake_sleep = MagicMock()
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", fake_sleep)

    result = _rsync_with_retry(["rsync"], timeout=60)
    assert result.returncode != 0
    assert fake_run.call_count == 1 + len(_RSYNC_RETRY_DELAYS)


def test_log_fn_receives_retry_notices(monkeypatch):
    """The caller's log_fn should see one notice per retry so the GUI
    can show 'retrying rsync 2/3 in 4s' messages."""
    fake_run = MagicMock(return_value=_fail(30))
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr("services.remote_runner.time.sleep", lambda s: None)

    logs: list[str] = []
    _rsync_with_retry(["rsync"], log_fn=logs.append)
    # One log line per retry (3 retries → 3 lines).
    retry_lines = [l for l in logs if "retry" in l.lower()]
    assert len(retry_lines) == len(_RSYNC_RETRY_DELAYS)
