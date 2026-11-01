"""Is the remote run still alive? One bounded ssh that asks, and an answer that says what it could not tell.

Three questions in the run's life need the same answer and had none:

* **The stream went silent** (tracker C1). Nothing distinguishes a well that is
  slow from a job the scheduler killed an hour ago or a connection that died
  without closing, so the app asks the host.
* **The run ended and its scheduler job was said to be closed** (tracker D3).
  "Job closed — the remote process has exited" was printed the moment the local
  ssh returned — including when it returned because the network went, which is
  exactly when a single-shot job keeps running (and billing). The job is now
  called closed only once the scheduler no longer lists it.
* **Stop was pressed with the network down** (tracker D3). The kill and the
  ``scancel`` are best-effort ssh round trips; when they cannot reach the host,
  the run was reported "stopped" while it went on.

:func:`probe` reads the run's pidfile and its ``.slurm`` sidecar on the host and
reports the pid (``kill -0``) and the job (``squeue``). It never cancels
anything. Every way it can fail to know — the host did not answer, the host has
no ``squeue``, the pid is on a login node this ssh did not land on — is a state
of its own rather than a guess, because "gone" read off a failed probe is the
"a failed remote command read as nothing there" pattern this review keeps
finding.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from services.remote_transport import _remote_quote
from services.slurm_ids import hold_check_shell, is_step, squeue_listing
from services.subprocess_text import TEXT_UTF8

#: How long one probe may take. It runs on a thread of its own, but a probe that
#: hangs is a probe that says nothing, so it is cut off and reported as such.
PROBE_TIMEOUT_S = 20

#: The scheduler states in which a job still holds (or is about to hold) a node.
_LIVE_JOB_STATES = {"PENDING", "RUNNING", "CONFIGURING", "SUSPENDED", "REQUEUED",
                    "RESIZING", "SIGNALING", "STAGE_OUT"}

UNREACHABLE = "unreachable"
ALIVE = "alive"
GONE = "gone"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Liveness:
    """What one probe found. *state* is one of the four module constants;
    *words* says it for a log line; *job_id* / *job_state* are the scheduler's
    answer when there was a job to ask about."""

    state: str
    words: str
    job_id: str = ""
    job_state: str = ""


def _script(pidfile: str, job_hint: str, staging_dir: str = "~/.allwell-staging") -> str:
    pid_q, sidecar_q = _remote_quote(pidfile), _remote_quote(f"{pidfile}.slurm")
    hint = job_hint if job_hint.isdigit() else ""
    return (
        f'{hold_check_shell(staging_dir)}'
        f'_pid=$(cat {pid_q} 2>/dev/null); _jid=$(cat {sidecar_q} 2>/dev/null); '
        f'[ -z "$_jid" ] && _jid="{hint}"; '
        'if [ -z "$_pid" ]; then echo "AW_PID none"; '
        'elif kill -0 "$_pid" 2>/dev/null; then echo "AW_PID $_pid alive"; '
        'else echo "AW_PID $_pid gone"; fi; '
        # A sidecar that names a granted node's own job says nothing about this run (the node outlives it), so
        # the pid decides; one that names a step (`<job>.<step>`) is asked about as a step.
        'if [ -n "$_jid" ] && ! _aw_is_hold "$_jid"; then '
        'if command -v squeue >/dev/null 2>&1; then '
        f'echo "AW_JOB $_jid $({squeue_listing("_jid")} 2>/dev/null | head -n1)"; '
        'else echo "AW_JOB $_jid ?"; fi; fi; echo AW_END'
    )


def probe(host, pidfile: str, *, scheduler: bool, job_hint: str = "",
          timeout_s: float = PROBE_TIMEOUT_S) -> Liveness:
    """Ask *host* whether the run behind *pidfile* is alive. Never raises.

    *scheduler* says the host runs jobs under a ``compute_launcher``; for those
    the scheduler's answer decides, since the pidfile names a process on a login
    node and a cluster may have several. *job_hint* is a job id the run's own
    stream named, asked about when the host has no sidecar (the job never
    started, so nothing inside it wrote one).
    """
    name = getattr(host, "name", "") or getattr(host, "ssh_alias", "the host")
    try:
        result = subprocess.run(host.ssh_argv(allocate_tty=False) + [
            _script(pidfile, job_hint, getattr(host, "remote_staging_dir", "~/.allwell-staging"))],
                                capture_output=True, timeout=timeout_s, **TEXT_UTF8)
    except subprocess.TimeoutExpired:
        return Liveness(UNREACHABLE, f"{name} did not answer within {int(timeout_s)} s")
    except OSError as exc:
        return Liveness(UNREACHABLE, f"could not run ssh to {name} ({exc})")
    out = result.stdout or ""
    if "AW_END" not in out:
        reason = (result.stderr or "").strip().splitlines()
        return Liveness(UNREACHABLE, f"{name} did not answer (ssh exit {result.returncode}"
                                     f"{': ' + reason[-1] if reason else ''})")
    fields = {line.split()[0]: line.split()[1:] for line in out.splitlines() if line.startswith("AW_")}
    pid = fields.get("AW_PID", ["none"])
    job = fields.get("AW_JOB")
    if scheduler and job:
        jid, state = job[0], (job[1] if len(job) > 1 else "")
        if state == "?":
            return Liveness(UNKNOWN, f"{name} has no squeue to ask about job {jid}", jid, "")
        if is_step(jid) and state:
            # A step has no state to report: `squeue -s` printing its id is the scheduler listing it.
            return Liveness(ALIVE, f"the scheduler lists step {jid}", jid, "RUNNING")
        if state.upper() in _LIVE_JOB_STATES:
            return Liveness(ALIVE, f"the scheduler lists job {jid} as {state.upper()}", jid, state.upper())
        return Liveness(GONE, f"the scheduler no longer lists job {jid}", jid, state.upper())
    if pid[0] == "none":
        if scheduler:
            return Liveness(GONE, f"{name} holds no job id or pid for this run")
        return Liveness(UNKNOWN, f"{name} holds no pid for this run")
    if len(pid) > 1 and pid[1] == "alive":
        return Liveness(ALIVE, f"process {pid[0]} is still running on {name}")
    # A pid this ssh cannot see may be on another login node, so on a cluster it
    # is not proof; on a single host it is.
    if scheduler:
        return Liveness(UNKNOWN, f"process {pid[0]} is not running on the login node this check reached")
    return Liveness(GONE, f"process {pid[0]} is no longer running on {name}")


def cancel_pending(host, job_id: str, *, timeout_s: float = PROBE_TIMEOUT_S) -> bool:
    """``scancel`` *job_id* if it is ours and still PENDING; whether the ssh ran.

    For a Stop or a Suspend pressed while the run's job was queued: the job has
    no ``.slurm`` sidecar yet (that is written from inside the allocation), so
    ``close_remote_job`` has no id to cancel, and the id the stream named is the
    only handle on it. Killing the local ``srun`` usually withdraws it; with the
    connection gone it does not. Owner-checked like every cancel this app sends
    (``remote_jobs._only_my_job``), and only for a job still waiting.
    """
    from services.remote_jobs import _only_my_job

    if not job_id.isdigit():
        return False
    # Never a granted node's own job: a run's teardown must not release the node (services/slurm_ids.py). The id
    # the stream named is a run's own queued job, so this only ever declines a mistake.
    script = (hold_check_shell(getattr(host, "remote_staging_dir", "~/.allwell-staging")) +
              f'_jid={job_id}; if _aw_is_hold "$_jid"; then echo AW_END; exit 0; fi; ' +
              _only_my_job("_jid", "AW_NOTMINE") +
              '[ "$(squeue -h -j "$_jid" -o %T 2>/dev/null | head -n1)" = PENDING ] && '
              'scancel -u "$_aw_me" "$_jid" 2>/dev/null; echo AW_END')
    try:
        result = subprocess.run(host.ssh_argv(allocate_tty=False) + [script],
                                capture_output=True, timeout=timeout_s, **TEXT_UTF8)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return "AW_END" in (result.stdout or "")
