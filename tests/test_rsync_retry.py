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


# The backoff is slept in small ``<=0.25s`` chunks (v5.2.28) so a long wait
# stays responsive to the Stop button — ``time.sleep`` therefore fires many
# times per retry rather than once. Reconstruct the per-retry totals from the
# chunk stream (the sleeps between consecutive subprocess.run calls) so the
# tests assert the *schedule*, not the chunking granularity.
_RUN = "run"


def _record_timeline(monkeypatch, result):
    """Patch subprocess.run + time.sleep to append to a shared timeline list.

    ``subprocess.run`` records a ``_RUN`` marker and returns ``result``;
    ``time.sleep`` records the slept duration. The interleaving lets a test
    reconstruct the per-retry backoff from the sleep chunks between runs.
    """
    timeline: list = []

    def _run(*_a, **_k):
        timeline.append(_RUN)
        return result

    monkeypatch.setattr("services.remote_runner.subprocess.run", _run)
    monkeypatch.setattr(
        "services.remote_runner.time.sleep", lambda s: timeline.append(float(s)),
    )
    return timeline


def _grouped_delays(timeline: list) -> tuple[float, ...]:
    """Sum the sleep chunks that fall between consecutive run calls."""
    groups: list[float] = []
    cur = 0.0
    seen_run = False
    for ev in timeline:
        if ev == _RUN:
            if seen_run:
                groups.append(round(cur, 6))
            cur = 0.0
            seen_run = True
        else:
            cur += ev
    return tuple(groups)


@pytest.mark.parametrize("code", sorted(_RSYNC_RETRY_CODES))
def test_transient_exit_code_triggers_retries(code, monkeypatch):
    """Every retry-eligible code keeps trying up to the budget."""
    fake_run = MagicMock(return_value=_fail(code))
    sleeps: list[float] = []
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr(
        "services.remote_runner.time.sleep", lambda s: sleeps.append(float(s)),
    )

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert result.returncode == code
    # 1 initial + 3 retries = 4 attempts.
    assert fake_run.call_count == 1 + len(_RSYNC_RETRY_DELAYS)
    # Total backoff equals the schedule, delivered in cancellable <=0.25s chunks.
    assert sleeps and all(s <= 0.25 + 1e-9 for s in sleeps)
    assert round(sum(sleeps), 6) == round(sum(_RSYNC_RETRY_DELAYS), 6)


def test_retry_then_succeed(monkeypatch):
    """A transient blip followed by recovery should succeed after one
    or two retries without exhausting the budget."""
    fake_run = MagicMock(side_effect=[_fail(10), _fail(10), _ok()])
    sleeps: list[float] = []
    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    monkeypatch.setattr(
        "services.remote_runner.time.sleep", lambda s: sleeps.append(float(s)),
    )

    result = _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert result.returncode == 0
    assert fake_run.call_count == 3
    # Two backoffs before the two retries: the first two scheduled delays.
    assert round(sum(sleeps), 6) == round(sum(_RSYNC_RETRY_DELAYS[:2]), 6)


def test_backoff_schedule_is_exponential(monkeypatch):
    """Backoffs before retries 1/2/3 must sum, per-retry, to _RSYNC_RETRY_DELAYS."""
    timeline = _record_timeline(monkeypatch, _fail(10))

    _rsync_with_retry(["rsync", "-a", "x", "y"])
    assert _grouped_delays(timeline) == _RSYNC_RETRY_DELAYS


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


# ── wall-clock deadline: a progressing transfer must not be killed ───────────

def test_cancellable_run_timeout_none_never_walltimeouts():
    """timeout=None disables the wall-clock deadline so a long-but-progressing
    transfer (the big final per-well zip) is never killed mid-flight — the
    fetch relies on rsync's own --timeout for stalls instead."""
    import sys
    import threading
    from services.remote_runner import _rsync_run_cancellable
    ev = threading.Event()  # never set
    r = _rsync_run_cancellable(
        [sys.executable, "-c", "import time; time.sleep(0.5)"],
        timeout=None, capture_output=True, cwd=None, stop_event=ev,
    )
    assert r.returncode == 0


def test_cancellable_run_walltimeout_still_fires_when_set():
    import sys
    import threading
    from services.remote_runner import _rsync_run_cancellable
    ev = threading.Event()
    with pytest.raises(subprocess.TimeoutExpired):
        _rsync_run_cancellable(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=1, capture_output=True, cwd=None, stop_event=ev,
        )
