"""A heartbeat on a slow shared filesystem: measured, given time to land, and kept out of the busy folder.

Reported on a GPU run with the shared-connection fix in place: "the run's
heartbeat did not reach the host … (ssh exit 124: the remote did not answer
within 30s: touch …)", several times in a row. The ssh connection was answering
— ``ssh_control`` probes it before calling a timeout a timeout, and would have
closed and replaced a dead one — so what did not finish in 30 s was the
``touch`` itself, on the cluster's shared filesystem.

Three changes answer it, and each has a test here:

* every beat that took ``SLOW_BEAT_S`` or longer is logged with its duration,
  so a run on such a filesystem leaves a record of how long the stalls were;
* a beat may take ``BEAT_TIMEOUT_S`` (75 s) to land, not the 30 s beat interval,
  and that allowance plus the interval still fits inside the remote's
  ``UPLOAD_HEARTBEAT_STALE_S``;
* the heartbeat lives in a folder of its own (``.aw_beat/``) rather than in the
  run directory, where batches are staged, renamed and deleted during the run —
  and every reader that decides whether a directory still has an owner also
  reads where an older app beats, so a session still running one is never taken
  for abandoned.
"""

from __future__ import annotations

import os
import subprocess
import time
import types
from pathlib import Path

from services.remote_heartbeat import BEAT_TIMEOUT_S, SLOW_BEAT_S, _UploadHeartbeat
from services.remote_run_id import (
    HEARTBEAT_DIR,
    LEGACY_UPLOAD_HEARTBEAT,
    UPLOAD_HEARTBEAT,
    remote_run_paths,
)
from services.remote_sweep import _target_script
from services.remote_wait_snippets import UPLOAD_HEARTBEAT_S, UPLOAD_HEARTBEAT_STALE_S


class _Host:
    """Answers each ssh command from a script of ``(seconds it takes, return code)``."""

    def __init__(self, clock, answers):
        self.clock = clock
        self.answers = list(answers)
        self.asked: list[tuple[list[str], float]] = []

    def _ssh_run(self, argv, *, check, timeout_s, **kw):
        self.asked.append((list(argv), timeout_s))
        took, rc = self.answers.pop(0)
        self.clock["t"] += took
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")


def _beat(answers, path="/r/reservations/tok/heartbeat"):
    clock = {"t": 0.0}
    host = _Host(clock, answers)
    logged: list[str] = []
    hb = _UploadHeartbeat(host, path, log_fn=logged.append, monotonic=lambda: clock["t"])
    return hb, host, logged


def test_a_beat_may_take_longer_than_the_interval_and_still_land_inside_the_remote_s_tolerance():
    assert BEAT_TIMEOUT_S == 75
    # The next beat starts UPLOAD_HEARTBEAT_S after the last landing; a beat landing at the very end of its
    # allowance must still beat the remote's staleness check.
    assert UPLOAD_HEARTBEAT_S + BEAT_TIMEOUT_S < UPLOAD_HEARTBEAT_STALE_S
    hb, host, _ = _beat([(1, 0)])
    hb.beat()
    assert host.asked == [(["touch", "/r/reservations/tok/heartbeat"], BEAT_TIMEOUT_S)]


def test_a_slow_beat_that_lands_is_logged_with_its_duration_and_is_not_a_miss():
    hb, _, logged = _beat([(41, 0), (41, 0), (41, 0)])
    for _ in range(3):
        hb.beat()
    assert logged == ["[remote] the heartbeat took 41 s to reach the host — the host's filesystem or "
                      "connection is slow to answer\n"] * 3
    assert hb._failures == 0


def test_a_slow_beat_that_fails_is_logged_with_its_duration_and_counted():
    hb, _, logged = _beat([(75, 124), (75, 124)])
    hb.beat()
    hb.beat()
    assert logged[0] == ("[remote] the heartbeat took 75 s to fail (ssh exit 124) — the host's filesystem or "
                         "connection is slow to answer\n")
    assert logged[2].startswith("[remote] WARNING the run's heartbeat did not reach the host 2 times in a row")


def test_a_quick_beat_logs_nothing():
    hb, _, logged = _beat([(SLOW_BEAT_S - 1, 0)])
    hb.beat()
    assert logged == []


def test_the_run_s_heartbeat_is_in_its_own_folder_which_the_first_beat_creates():
    paths = remote_run_paths(types.SimpleNamespace(remote_staging_dir="/s"), "run-1")
    assert paths.heartbeat == f"/s/runs/run-1/{HEARTBEAT_DIR}/alive" == f"/s/runs/run-1/{UPLOAD_HEARTBEAT}"
    hb, host, _ = _beat([(0, 0), (0, 0), (0, 0)], path=paths.heartbeat)
    hb.beat()
    hb.beat()
    assert [argv for argv, _ in host.asked] == [
        ["mkdir", "-p", f"/s/runs/run-1/{HEARTBEAT_DIR}"],
        ["touch", paths.heartbeat],
        ["touch", paths.heartbeat],
    ]


def test_a_failed_mkdir_is_tried_again_by_the_next_beat():
    hb, host, _ = _beat([(0, 255), (0, 1), (0, 0), (0, 0)], path=f"/s/runs/r/{UPLOAD_HEARTBEAT}")
    hb.beat()
    hb.beat()
    assert [argv[0] for argv, _ in host.asked] == ["mkdir", "touch", "mkdir", "touch"]


def _sweep_one(tmp_path: Path, *, fresh: str) -> str:
    """Run the real sweep shell for one run directory whose job ``squeue`` lists as running, with the sidecar an
    hour old and only the heartbeat named by *fresh* touched now."""
    run_dir = tmp_path / "runs" / "run-1"
    (run_dir / HEARTBEAT_DIR).mkdir(parents=True)
    sidecar = run_dir / "run.pid.slurm"
    sidecar.write_text("4242\n")
    hour_ago = time.time() - 3600
    os.utime(sidecar, (hour_ago, hour_ago))
    (run_dir / fresh).touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool, says in (("squeue", "echo RUNNING"), ("scancel", "exit 0")):
        (bin_dir / tool).write_text(f"#!/bin/sh\n{says}\n")
        (bin_dir / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    script = _target_script(0, str(run_dir / "run.pid"), str(run_dir / UPLOAD_HEARTBEAT))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20, env=env).stdout


def test_the_sweep_counts_the_heartbeat_in_its_folder_as_an_owner(tmp_path):
    assert _sweep_one(tmp_path, fresh=UPLOAD_HEARTBEAT).startswith("AW_OWNED 0 4242")


def test_the_sweep_counts_an_older_app_s_heartbeat_as_an_owner(tmp_path):
    """Two windows on one plate, one still on an app that beats the run directory's root: the newer one's sweep
    must not take that session's live job for abandoned and cancel it."""
    assert _sweep_one(tmp_path, fresh=LEGACY_UPLOAD_HEARTBEAT).startswith("AW_OWNED 0 4242")
