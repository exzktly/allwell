"""Auto-delete after a verified run: earlier attempts go too, held wells and live jobs stay (tracker F4, F6).

F6: each Resume left its predecessor's whole run directory behind — Auto-delete removed only the current run's,
and a re-attached run left the directory it borrowed "to remove by hand" — so every resume that moved to a new
address orphaned an attempt (a nanowell well is about 8 GB) until the remote quota filled.

F4: a nanowell run suspended before its end-of-run re-anchoring holds wells in ``keep/`` for the resumed run's pass,
which re-runs them from their output in that run's directory. Auto-delete after the suspend's verification removed
the run directory, ``keep/`` with it, and the next run said "no well was held for it".

These drive the real ``RemoteRunner.verify_and_cleanup`` against a local directory standing in for the host: the
remote commands run under ``bash`` (``squeue`` is a stand-in on ``PATH`` where a test needs a job), the run journal
is the real one, and only verification's verdict is given (it passes).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from services import run_journal as J
from services.remote_config import RemoteHost
from services.remote_fetch import FetchedOutputVerifier, FetchVerification
from services.remote_run_state import LocalInputMode, _RemoteRun
from services.remote_runner import RemoteRunner
from services.remote_transport import _remote_join
from services.remote_upload import DatasetCache


def _host(staging: Path) -> RemoteHost:
    return RemoteHost(name="t", ssh_alias="h", remote_project_dir="~/allwell", remote_staging_dir=str(staging),
                      remote_python="/opt/python", cpu_only=False, extra_args=(), cache_budget_gb=10,
                      measured_bandwidth_mbps=100.0)


def _remote(tmp_path, monkeypatch, *, squeue_lists: str | None = None):
    """The host is this machine's bash; *squeue_lists* is the job id a stand-in ``squeue`` reports running."""
    env = dict(os.environ)
    if squeue_lists is not None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "squeue").write_text(f"#!/bin/sh\necho {squeue_lists}\n")
        (bin_dir / "squeue").chmod(0o755)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"

    def run(script):
        done = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=20, env=env)
        return subprocess.CompletedProcess(["bash"], done.returncode, done.stdout, done.stderr)

    monkeypatch.setattr(DatasetCache, "_ssh_script", lambda self, script, *, check: run(script))
    monkeypatch.setattr(DatasetCache, "_ssh_run", lambda self, argv, *, check, **k: run(_remote_join(argv)))
    monkeypatch.setattr(FetchedOutputVerifier, "verify", lambda self, ok, *, log, **k: FetchVerification(passed=True))
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")


def _attempts(tmp_path, *run_ids: str, out_of=lambda run_id: "run-1"):
    """Journal *run_ids* in order, each writing into ``out_of(run_id)/out`` — re-attached, as a resume is."""
    staging, local = tmp_path / "remote", tmp_path / "local"
    local.mkdir(exist_ok=True)
    for n, run_id in enumerate(run_ids):
        for sub in ("in", "out"):
            (staging / "runs" / run_id / sub).mkdir(parents=True, exist_ok=True)
        J.open_run(local, spec={}, mode="remote-batched", input_dir="/in", argv=["--x"])
        J.record_remote(local, run_id=run_id, cache_key=f"k{n}", host_alias="h",
                        remote_input_dir=str(staging / "runs" / run_id / "in"),
                        remote_output_dir=str(staging / "runs" / out_of(run_id) / "out"),
                        argv_digest="d", source_key="s")
    return staging, local


def _runner(staging, local, run_id: str, *, out_of: str, nanowell=False) -> RemoteRunner:
    runner = RemoteRunner(_host(staging))
    runner._run = _RemoteRun(
        run_id=run_id, cache_key="k", remote_input_dir=str(staging / "runs" / run_id / "in"),
        remote_output_dir=str(staging / "runs" / out_of / "out"), remote_pidfile=str(staging / "runs" / run_id / "run.pid"),
        local_output_dir=local, local_input_mode=LocalInputMode.ZIP, nanowell=nanowell,
        reattached=out_of != run_id)
    return runner


def _cleanup(runner) -> str:
    logged: list[str] = []
    assert runner.verify_and_cleanup(True, cleanup_mode="auto", log_fn=logged.append)
    return "".join(logged)


def _finished(staging, run_id: str, well: str, local: Path | None) -> None:
    (staging / "runs" / run_id / "out" / f"{well}_out.zip").write_bytes(b"zip")
    if local is not None:
        (local / f"measurements_{well}.csv.gz").write_bytes(b"rows")


def test_a_verified_resume_removes_the_attempts_before_it(tmp_path, monkeypatch):
    _remote(tmp_path, monkeypatch)
    staging, local = _attempts(tmp_path, "run-1", "run-2", "run-3")
    _finished(staging, "run-1", "A01", local)
    log = _cleanup(_runner(staging, local, "run-3", out_of="run-1"))
    left = sorted(p.name for p in (staging / "runs").iterdir())
    assert left == [], f"earlier attempts' run directories survived a verified, auto-deleted resume: {left}\n{log}"
    assert "removed an earlier attempt's run dir" in log


def test_an_attempt_whose_job_may_still_run_is_kept(tmp_path, monkeypatch):
    _remote(tmp_path, monkeypatch, squeue_lists="4242")
    staging, local = _attempts(tmp_path, "run-1", "run-2", "run-3")
    (staging / "runs" / "run-2" / "run.pid.slurm").write_text("4242\n")
    log = _cleanup(_runner(staging, local, "run-3", out_of="run-1"))
    assert (staging / "runs" / "run-2").is_dir(), f"a run directory whose job is still running was removed\n{log}"
    assert "its scheduler job is still running" in log
    assert not (staging / "runs" / "run-1").exists(), "an idle attempt beside it was kept as well"


def test_an_attempt_beating_its_heartbeat_is_kept(tmp_path, monkeypatch):
    from services.remote_run_id import UPLOAD_HEARTBEAT

    _remote(tmp_path, monkeypatch)
    staging, local = _attempts(tmp_path, "run-1", "run-2", "run-3")
    (staging / "runs" / "run-2" / UPLOAD_HEARTBEAT).touch()
    log = _cleanup(_runner(staging, local, "run-3", out_of="run-1"))
    assert (staging / "runs" / "run-2").is_dir() and "heartbeat" in log, log


def test_an_attempt_holding_output_not_brought_home_is_kept(tmp_path, monkeypatch):
    _remote(tmp_path, monkeypatch)
    staging, local = _attempts(tmp_path, "run-1", "run-x", "run-3",
                               out_of=lambda run_id: "run-x" if run_id == "run-x" else "run-1")
    _finished(staging, "run-x", "B02", None)                      # finished there, never downloaded here
    log = _cleanup(_runner(staging, local, "run-3", out_of="run-1"))
    assert (staging / "runs" / "run-x" / "out" / "B02_out.zip").exists(), log
    assert "not downloaded here (B02" in log, log


def test_a_suspended_nanowell_run_keeps_the_wells_it_held(tmp_path, monkeypatch):
    """F4: suspended before its final pass, with Auto-delete on."""
    _remote(tmp_path, monkeypatch)
    staging, local = _attempts(tmp_path, "run-1", out_of=lambda run_id: run_id)
    keep = staging / "runs" / "run-1" / "keep"
    keep.mkdir()
    (keep / "A02.zip").write_bytes(b"held")
    _finished(staging, "run-1", "A02", local)
    log = _cleanup(_runner(staging, local, "run-1", out_of="run-1", nanowell=True))
    assert (keep / "A02.zip").exists() and (staging / "runs" / "run-1" / "out" / "A02_out.zip").exists(), (
        f"Auto-delete removed the wells held for the resumed run's re-anchoring, and the output it re-runs them "
        f"from\n{log}")
    assert "waiting for the end-of-run re-anchoring" in log


def test_without_held_wells_the_nanowell_run_dir_goes(tmp_path, monkeypatch):
    _remote(tmp_path, monkeypatch)
    staging, local = _attempts(tmp_path, "run-1", out_of=lambda run_id: run_id)
    (staging / "runs" / "run-1" / "keep").mkdir()
    _cleanup(_runner(staging, local, "run-1", out_of="run-1", nanowell=True))
    assert not (staging / "runs" / "run-1").exists()


@pytest.mark.parametrize("age_s, kept", [(5, True), (3600, False)])
def test_the_heartbeat_age_decides(tmp_path, monkeypatch, age_s, kept):
    from services.remote_run_id import UPLOAD_HEARTBEAT

    _remote(tmp_path, monkeypatch)
    staging, local = _attempts(tmp_path, "run-1", "run-2")
    beat = staging / "runs" / "run-1" / UPLOAD_HEARTBEAT
    beat.touch()
    os.utime(beat, (time.time() - age_s, time.time() - age_s))
    _cleanup(_runner(staging, local, "run-2", out_of="run-1"))
    assert (staging / "runs" / "run-1").is_dir() is kept
