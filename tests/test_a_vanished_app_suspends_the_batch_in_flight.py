"""An app that disappears while a batch is running hands the node back.

The batch driver used to ask "is the app still there?" only *between* batches.
An app that crashed or was force-quit mid-batch leaves its ssh child running as
an orphan, so the srun holding the allocation stayed alive and the node was kept
for the rest of the batch — hours on a large one — with nothing left to fetch
the result. The watchdog beside each batch answers a heartbeat that has stopped
by writing the suspend request: the batch winds down keeping what it finished,
and the driver then exits instead of waiting for a batch that will never come.

These run the real driver shell in bash, with the watchdog's clocks shortened.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

import services.remote_commands as rc


def _run(tmp_path, monkeypatch, *, heartbeat_age_s: float, timeout: float):
    from services.remote_run_id import SUSPEND_REQUEST

    monkeypatch.setattr(rc, "MID_BATCH_HEARTBEAT_STALE_S", 2)
    monkeypatch.setattr(rc, "MID_BATCH_WATCH_POLL_S", 1)
    run_dir = tmp_path / "run"
    (run_dir / "b1").mkdir(parents=True)
    (run_dir / rc.BATCH_READY.format(i=1)).touch()
    heartbeat = run_dir / "heartbeat"
    heartbeat.touch()
    then = time.time() - heartbeat_age_s
    os.utime(heartbeat, (then, then))
    request = run_dir / SUSPEND_REQUEST
    seen = run_dir / "pipeline_saw_the_request"
    # The "pipeline" for batch 1 runs as a real one does with `--suspend_file`:
    # it watches the request and winds down when it appears — here, for at most
    # six seconds, after which it finishes on its own.
    body = (f"for i in 1 2 3 4 5 6 7 8 9 10 11 12; do "
            f"[ -f {request} ] && {{ touch {seen}; exit 0; }}; sleep 0.5; done")
    snippet = rc._batch_driver_snippet(
        str(run_dir), 3, ["bash", "-c", body],
        heartbeat=str(heartbeat), suspend_request=str(request))
    return subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=timeout), seen


def test_a_silent_app_suspends_the_batch_and_the_driver_exits(tmp_path, monkeypatch):
    out, seen = _run(tmp_path, monkeypatch, heartbeat_age_s=3600, timeout=15)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "stopped responding mid-batch" in out.stdout
    assert seen.exists(), "the batch in flight was never told to wind down"
    assert "suspended — no further batches" in out.stdout
    assert "batch 2 starting" not in out.stdout


def test_a_beating_app_leaves_the_batch_alone(tmp_path, monkeypatch):
    """The control: a fresh heartbeat is not mistaken for a vanished app.

    The batch runs to its own end without a request, and the driver goes on to
    wait for batch 2 — which never arrives, so the run is cut off by the timeout.
    """
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        # Stamped 30 s ahead, so it stays fresh for the whole run without a
        # thread touching it. The pipeline is done within ~6 s.
        _run(tmp_path, monkeypatch, heartbeat_age_s=-30, timeout=9)
    stdout = exc.value.stdout or b""
    text = stdout.decode() if isinstance(stdout, bytes) else stdout
    assert "stopped responding" not in text
    assert not (tmp_path / "run" / "pipeline_saw_the_request").exists()
    assert "waiting for batch 2" in text
