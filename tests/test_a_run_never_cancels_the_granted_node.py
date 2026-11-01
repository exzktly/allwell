"""A run on a granted node ends its own step; nothing a run's teardown does releases the node (tracker H4, H6).

A granted node (``services.remote_hold``) is a Slurm job the user is keeping. A run on it is a *step* of that job,
and the run's sidecar (``<pidfile>.slurm``) names the step, ``<job>.<step>``. Every reader of that sidecar used to
assume the id names a job the run owns outright, so each would, unchanged, have released the node when a run ended:
cancelling the job (``close_remote_job``, the orphan sweep), reading the step as a finished job because
``squeue -j 4001.3`` is "Invalid job id" (the liveness probe, the clean-up of earlier attempts), or deleting the
run directory of a step that was still going.

These run the real shell each of those sends against stand-in ``ssh`` / ``squeue`` / ``scancel``
(``tests/_remote_standins.py``), whose ``squeue`` rejects ``-j <job>.<step>`` and answers ``-s<job>.<step>`` the
way Slurm does. The wrong implementations they are written to reject: cancelling the job part of a step id;
asking ``squeue -j`` about a step; owner-checking the step id and so finding no owner; cancelling a sidecar that
names a hold's own job; and, on the other side, a guard so broad that an ordinary run's job is no longer cancelled.
"""

from __future__ import annotations

import os
import subprocess
import types

import pytest

from services.remote_config import RemoteHost
from services.remote_jobs import close_remote_job
from services.remote_liveness import ALIVE, GONE, cancel_pending, probe
from services.remote_reuse import _REMOVE_IF_IDLE
from services.remote_run_id import UPLOAD_HEARTBEAT
from services.remote_sweep import sweep_orphaned_allocation
from tests import _remote_standins

RUN_DIR = "~/.allwell-staging/runs/r1"
PIDFILE = f"{RUN_DIR}/run.pid"
HEARTBEAT = f"{RUN_DIR}/{UPLOAD_HEARTBEAT}"
HOLD = "4001"          # the granted node's own job


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    return _remote_standins.install(tmp_path, monkeypatch)


@pytest.fixture
def host():
    return RemoteHost(name="hpc", ssh_alias="hpc", compute_launcher="srun --partition=gpu")


def _granted_node(cluster) -> None:
    """The hold job is running with a step of this run in it, as a granted node with a run looks."""
    cluster.add_job(HOLD)
    cluster.add_step(f"{HOLD}.3")
    cluster.write("~/.allwell-staging/holds/abc123/job", HOLD)


def _run() -> types.SimpleNamespace:
    return types.SimpleNamespace(remote_pidfile=PIDFILE, owns_remote_job=True)


def _log() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


# ── ending the run ────────────────────────────────────────────────────────────────────────────────────────────────

def test_closing_a_run_on_a_granted_node_ends_its_step_and_leaves_the_node(cluster, host):
    """Wrong implementation: cancelling the job part (4001), which is the node."""
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.3")
    lines, log = _log()
    close_remote_job(host, _run(), log_fn=log)
    assert cluster.cancelled == [f"{HOLD}.3"]
    assert HOLD in cluster.jobs, "the granted node was cancelled by the end of a run"
    assert cluster.steps == []
    assert any("ended the run's step 4001.3" in line and "stays granted" in line for line in lines)


def test_a_sidecar_naming_the_nodes_own_job_is_never_cancelled(cluster, host):
    """Wrong implementation: today's, which cancels whatever the sidecar names."""
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", HOLD)
    cluster.write(HEARTBEAT, "")
    lines, log = _log()
    close_remote_job(host, _run(), log_fn=log)
    assert cluster.cancelled == []
    assert HOLD in cluster.jobs
    assert any("granted node's own job" in line and "left granted" in line for line in lines)
    # The run is over: its heartbeat must not read as a live owner and refuse the next Resume.
    assert not (cluster.home / HEARTBEAT.removeprefix("~/")).exists()


@pytest.mark.parametrize("step", ["batch", "extern"])
def test_the_nodes_own_batch_and_extern_steps_are_never_cancelled(cluster, host, step):
    """Cancelling `<hold>.batch` kills the hold script: it is the node itself."""
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.{step}")
    close_remote_job(host, _run(), log_fn=lambda _m: None)
    assert cluster.cancelled == []


def test_an_ordinary_runs_job_is_still_cancelled(cluster, host):
    """The guard must not be so broad that a run's own allocation outlives it: there is a granted node here, and
    the run's job is another one."""
    _granted_node(cluster)
    cluster.add_job("5000")
    cluster.write(f"{PIDFILE}.slurm", "5000")
    close_remote_job(host, _run(), log_fn=lambda _m: None)
    assert cluster.cancelled == ["5000"]
    assert HOLD in cluster.jobs


def test_a_step_of_a_job_that_is_someone_elses_is_not_cancelled(cluster, host):
    """Wrong implementation: owner-checking the step id, which `squeue -j` rejects, so no owner is found and the
    cancel goes ahead."""
    cluster.add_job("6000")
    cluster.add_step("6000.1")
    (cluster.root / "jobs").write_text("6000 RUNNING somebody-else\n")
    cluster.write(f"{PIDFILE}.slurm", "6000.1")
    lines, log = _log()
    close_remote_job(host, _run(), log_fn=log)
    assert cluster.cancelled == []
    assert any("belongs to somebody-else" in line for line in lines)


# ── asking whether the run is alive ───────────────────────────────────────────────────────────────────────────────

def test_a_run_whose_step_is_listed_is_alive(cluster, host):
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.3")
    verdict = probe(host, PIDFILE, scheduler=True)
    assert verdict.state == ALIVE and verdict.job_id == f"{HOLD}.3"


def test_a_run_whose_step_has_ended_is_gone_though_the_node_is_alive(cluster, host):
    """Wrong implementations: asking about the job part (the node is RUNNING, so the run would be alive for as
    long as the node is), or `squeue -j <step>` (an error, which reads as a finished job but only by accident)."""
    cluster.add_job(HOLD)
    cluster.write("~/.allwell-staging/holds/abc123/job", HOLD)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.3")           # no such step is listed any more
    verdict = probe(host, PIDFILE, scheduler=True)
    assert verdict.state == GONE and verdict.job_id == f"{HOLD}.3"


def test_a_sidecar_naming_the_node_does_not_make_a_finished_run_look_alive(cluster, host):
    """The node is RUNNING for as long as it is kept; a run whose sidecar names it must be judged by its process."""
    cluster.add_job(HOLD)
    cluster.write("~/.allwell-staging/holds/abc123/job", HOLD)
    cluster.write(f"{PIDFILE}.slurm", HOLD)
    verdict = probe(host, PIDFILE, scheduler=True)
    assert verdict.state != ALIVE and verdict.job_id == ""


def test_an_ordinary_job_is_still_judged_by_the_scheduler(cluster, host):
    cluster.add_job("5000")
    cluster.write(f"{PIDFILE}.slurm", "5000")
    verdict = probe(host, PIDFILE, scheduler=True)
    assert verdict.state == ALIVE and verdict.job_id == "5000"


def test_cancel_pending_declines_the_nodes_own_job(cluster, host):
    _granted_node(cluster)
    (cluster.root / "jobs").write_text(f"{HOLD} PENDING {cluster.user}\n")
    assert cancel_pending(host, HOLD) is True        # the ssh ran…
    assert cluster.cancelled == []                    # …and declined


def test_cancel_pending_still_cancels_a_pending_run_job(cluster, host):
    _granted_node(cluster)
    (cluster.root / "jobs").write_text(f"{HOLD} RUNNING {cluster.user}\n5000 PENDING {cluster.user}\n")
    assert cancel_pending(host, "5000") is True
    assert cluster.cancelled == ["5000"]


# ── the orphan sweep ──────────────────────────────────────────────────────────────────────────────────────────────

def _age(cluster, remote_path: str, seconds: int) -> None:
    path = cluster.home / remote_path.removeprefix("~/")
    t = os.stat(path).st_mtime - seconds
    os.utime(path, (t, t))


def test_the_sweep_of_a_leftover_step_cancels_the_step_and_not_the_node(cluster, host):
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.3")
    _age(cluster, f"{PIDFILE}.slurm", 3600)
    lines, log = _log()
    assert sweep_orphaned_allocation(host, PIDFILE, HEARTBEAT, log_fn=log) == "swept"
    assert cluster.cancelled == [f"{HOLD}.3"]
    assert HOLD in cluster.jobs


def test_the_sweep_does_not_cancel_a_live_step_whose_app_is_beating(cluster, host):
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.3")
    _age(cluster, f"{PIDFILE}.slurm", 3600)
    cluster.write(HEARTBEAT, "")                       # a fresh heartbeat: another session owns it
    assert sweep_orphaned_allocation(host, PIDFILE, HEARTBEAT, log_fn=lambda _m: None) == "owned"
    assert cluster.cancelled == []


def test_the_sweep_forgets_a_step_that_has_ended(cluster, host):
    """Wrong implementation: `squeue -j <step>` is an error that this code happens to read as gone — and a step
    that is alive would be read the same way. Here the step is listed in the other test and gone here."""
    cluster.add_job(HOLD)
    cluster.write("~/.allwell-staging/holds/abc123/job", HOLD)
    cluster.write(f"{PIDFILE}.slurm", f"{HOLD}.3")
    _age(cluster, f"{PIDFILE}.slurm", 3600)
    assert sweep_orphaned_allocation(host, PIDFILE, HEARTBEAT, log_fn=lambda _m: None) == "none"
    assert cluster.cancelled == []
    assert not (cluster.home / (PIDFILE + ".slurm").removeprefix("~/")).exists()


def test_the_sweep_leaves_a_sidecar_that_names_the_node_itself(cluster, host):
    _granted_node(cluster)
    cluster.write(f"{PIDFILE}.slurm", HOLD)
    _age(cluster, f"{PIDFILE}.slurm", 3600)
    lines, log = _log()
    assert sweep_orphaned_allocation(host, PIDFILE, HEARTBEAT, log_fn=log) == "none"
    assert cluster.cancelled == [] and HOLD in cluster.jobs
    assert any("granted node" in line for line in lines)


def test_the_sweep_still_cancels_an_ordinary_leftover_job(cluster, host):
    _granted_node(cluster)
    cluster.add_job("5000")
    cluster.write(f"{PIDFILE}.slurm", "5000")
    _age(cluster, f"{PIDFILE}.slurm", 3600)
    assert sweep_orphaned_allocation(host, PIDFILE, HEARTBEAT, log_fn=lambda _m: None) == "swept"
    assert cluster.cancelled == ["5000"]


# ── clearing earlier attempts' run directories ────────────────────────────────────────────────────────────────────

def _clean(cluster, run_dir: str) -> str:
    """Run the clean-up shell on *run_dir* with the stand-in host's HOME; what it printed."""
    from services.remote_commands import HOLD_DIR
    from services.remote_run_id import LEGACY_UPLOAD_HEARTBEAT, UPLOAD_HEARTBEAT

    fn = _REMOVE_IF_IDLE.format(hold=HOLD_DIR, heartbeat=UPLOAD_HEARTBEAT, legacy=LEGACY_UPLOAD_HEARTBEAT, stale=120)
    env = {**os.environ, "AW_STANDIN": str(cluster.root), "HOME": str(cluster.home)}
    done = subprocess.run(["bash", "-c", f'{fn}\n_aw_clean "{run_dir}"'], capture_output=True, text=True, env=env)
    return done.stdout.strip()


def test_an_earlier_attempts_directory_is_kept_while_its_step_is_listed(cluster):
    """Wrong implementation: `squeue -j <step>` answers "Invalid job id", which this clean-up reads as a finished
    job — and removes the run directory a running step is writing into."""
    _granted_node(cluster)
    cluster.write(f"{RUN_DIR}/run.pid.slurm", f"{HOLD}.3")
    out = _clean(cluster, str(cluster.home / RUN_DIR.removeprefix("~/")))
    assert out.startswith("AW_LIVE") and out.endswith("job"), out
    assert (cluster.home / RUN_DIR.removeprefix("~/")).exists()


def test_an_earlier_attempts_directory_is_removed_once_its_step_has_ended(cluster):
    cluster.add_job(HOLD)                                  # the node is still there; the step is not
    cluster.write(f"{RUN_DIR}/run.pid.slurm", f"{HOLD}.3")
    out = _clean(cluster, str(cluster.home / RUN_DIR.removeprefix("~/")))
    assert out.startswith("AW_GONE"), out
    assert not (cluster.home / RUN_DIR.removeprefix("~/")).exists()
