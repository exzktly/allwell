"""Scheduler jobs a remote run holds, and making sure none outlives it.

On a host with a ``compute_launcher`` the pipeline runs inside a scheduler
allocation. :func:`close_remote_job` is the guaranteed-teardown backstop every
exit the app survives goes through; ``services.remote_sweep`` is what the
*next* run of the same plate does about an exit it did not survive — a
SIGKILL, a segfault, a power cut. Neither will cancel a job that belongs to
anybody else (:func:`_only_my_job`), and nothing they cannot measure is treated
as dead.
"""

from __future__ import annotations

import logging
import posixpath
import subprocess
from collections.abc import Callable

from services.remote_config import RemoteHost
from services.remote_run_id import UPLOAD_HEARTBEAT
from services.remote_run_state import _RemoteRun
from services.remote_transport import _remote_quote
from services.subprocess_text import TEXT_UTF8


def _only_my_job(jid_var: str, marker: str) -> str:
    """Shell that refuses to cancel a job belonging to somebody else.

    Two locks rather than one. The id is read from a file this app wrote, so in
    the ordinary case it is our own job — but ``remote_staging_dir`` can point
    at shared scratch, and ``run_id`` is deterministic in (input, arguments),
    so two people processing the same plate share a run directory and therefore
    a sidecar. Slurm would refuse a non-owner's ``scancel`` anyway; that is the
    scheduler's policy, not this app's promise, and a cluster where operators
    run the GUI would not have it.

    So: ask the scheduler whose job it is and stop if the answer is not us, and
    pass ``-u`` to ``scancel`` as well, so even a job that changed hands between
    the question and the answer cannot be cancelled by us.
    """
    return (
        f'_aw_me=$(id -un 2>/dev/null || echo "$USER"); '
        f'_aw_owner=$(squeue -h -j "${jid_var}" -o %u 2>/dev/null | head -n1); '
        f'if [ -n "$_aw_owner" ] && [ "$_aw_owner" != "$_aw_me" ]; then '
        f'echo "{marker} ${jid_var} $_aw_owner"; exit 0; fi; '
    )


def kill_remote_pipeline(host: RemoteHost, run: _RemoteRun) -> None:
    """``kill -TERM`` the PID the run recorded in its pidfile, over a fresh ssh.

    The second channel :meth:`RemoteRunner.cancel` adds to the local
    process-group kill, which is the primary one. The caller checks that the
    run launched (``run.owns_remote_job``): until then the pidfile holds what
    the last run of this plate wrote, which may be another session's ``srun``.
    """
    pidfile_q = _remote_quote(run.remote_pidfile)
    try:
        subprocess.run(
            host.ssh_argv(allocate_tty=False) + [
                f"kill -TERM $(cat {pidfile_q} 2>/dev/null) 2>/dev/null || true"
            ],
            capture_output=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        # Best-effort (the local process-group kill is primary), but don't
        # swallow it silently — record that this teardown channel failed
        # (audit E13); close_remote_job() is the scheduler backstop.
        logging.getLogger(__name__).warning(
            "remote cancel: ssh kill failed (%s); relying on the local "
            "process-group kill + scancel backstop", exc)


def close_remote_job(
    host: RemoteHost, run: _RemoteRun | None,
    *, log_fn: Callable[[str], None] | None = None,
) -> None:
    """Guaranteed-teardown backstop: ensure the scheduler job this run
    launched is closed.

    Idempotent and best-effort — a no-op on direct-execution hosts (no
    ``compute_launcher``), when no run has started, when this run never
    launched an allocation of its own (``run.owns_remote_job``), or when the
    job has already exited (``scancel`` on a finished id is harmless). Reads the
    ``$SLURM_JOB_ID`` the run wrote to ``<pidfile>.slurm`` on the shared
    filesystem and ``scancel``s it via a fresh ssh.

    ``srun`` exits with the pipeline, so usually the job is gone already;
    this makes sure an *orphaned* allocation (ssh / srun client died,
    connection dropped, AllWell crashed) never lingers. Called from the
    PipelineRunner (complete / fail / exception) and from :meth:`cancel`
    (user Stop); ``services.remote_liveness`` then confirms it."""
    if not host.compute_launcher.strip():
        return
    if run is None:
        return
    if not getattr(run, "owns_remote_job", False):
        # Refused, or failed, before it launched: the sidecar it would read is
        # whatever the last run of this plate left — and when the refusal was
        # "another session is running this dataset", it is that session's live
        # job. A run that asked for nothing has nothing to hand back.
        return
    log = log_fn or (lambda _m: None)
    slurm_sidecar_q = _remote_quote(f"{run.remote_pidfile}.slurm")
    # Once cancelled, the run's own last heartbeat would read as a live owner
    # for two more minutes and refuse the Resume pressed right after Stop. The
    # sidecar stays: the next run's sweep asks squeue whether the job is gone.
    heartbeat_q = _remote_quote(f"{posixpath.dirname(run.remote_pidfile)}/{UPLOAD_HEARTBEAT}")
    cmd = (
        f"_jid=$(cat {slurm_sidecar_q} 2>/dev/null); "
        '[ -z "$_jid" ] && exit 0; '
        + _only_my_job("_jid", "AW_NOTMINE") +
        'command -v scancel >/dev/null 2>&1 '
        '&& scancel -u "$_aw_me" "$_jid" 2>/dev/null '
        f'&& {{ rm -f {heartbeat_q}; echo "AW_SCANCELLED $_jid"; }} || true'
    )
    try:
        result = subprocess.run(
            host.ssh_argv(allocate_tty=False) + [cmd],
            capture_output=True, timeout=10, **TEXT_UTF8,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        # A failed teardown ssh must not be silent — an orphaned SLURM
        # allocation could keep running (and billing) unnoticed (audit E13).
        log(f"[remote] WARN scheduler: could not confirm scancel of the "
            f"remote job ({exc}); a SLURM allocation may still be running.\n")
        return
    out = (result.stdout or "")
    if result.returncode == 255:      # ssh's own failure: nothing ran, so nothing is known
        log("[remote] WARN scheduler: could not reach the host to cancel the job (ssh exit 255).\n")
        return
    if "AW_SCANCELLED" in out:
        jid = out.split("AW_SCANCELLED", 1)[1].strip().split()[0:1]
        log(
            f"[remote] scheduler: cancelled lingering job "
            f"{jid[0] if jid else ''}.\n"
        )
    elif "AW_NOTMINE" in out:
        # Not "already finished": it is running, and it is somebody else's.
        # Reporting it as a clean teardown would say the opposite of what
        # happened, and this is the log a user reads when a node is still
        # busy after they stopped.
        rest = out.split("AW_NOTMINE", 1)[1].strip().split()
        log(f"[remote] scheduler: job {rest[0] if rest else '?'} belongs to "
            f"{rest[1] if len(rest) > 1 else 'another user'} — left "
            f"running.\n")
    else:
        # The common case: ``srun`` already exited with the pipeline, so
        # there was nothing left to cancel — which scancel cannot confirm
        # (its exit status is discarded); the caller asks squeue.
        log("[remote] scheduler: no job left to cancel.\n")
