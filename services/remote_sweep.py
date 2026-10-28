"""The next run's sweep of an allocation an earlier run of the same plate left.

:func:`services.remote_jobs.close_remote_job` covers every exit the app
survives. This covers the ones it does not — a SIGKILL, a segfault, a power
cut — by reading the job id the crashed run left on the shared filesystem
(``<pidfile>.slurm``) and cancelling it when nobody owns it any more.

**A job is somebody's only while the scheduler still runs it.** The sidecar
outlives its job, and the heartbeat outlives its app by up to
:data:`UPLOAD_HEARTBEAT_STALE_S`, so neither can say on its own that anybody is
still using the directory: a fresh heartbeat beside a sidecar naming a job that
had ended used to read as "another session is running this dataset", and
refused the Resume a user pressed right after Stop, naming a session that did
not exist. ``squeue`` is asked first; a job it no longer lists as alive has
ended, its sidecar is removed, and the directory is nobody's.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable

from services.remote_config import RemoteHost
from services.remote_jobs import _only_my_job
from services.remote_transport import _remote_quote
from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S
from services.subprocess_text import TEXT_UTF8

#: ``squeue %T`` states of a job that has ended. Anything else it lists —
#: ``PENDING``, ``RUNNING``, ``COMPLETING``, ``SUSPENDED`` … — is still alive:
#: a ``COMPLETING`` job's processes may still be writing.
_ENDED_STATES = ("COMPLETED", "CANCELLED*", "FAILED", "TIMEOUT", "NODE_FAIL",
                 "PREEMPTED", "BOOT_FAIL", "DEADLINE", "OUT_OF_MEMORY")

#: How long the sweep waits for a job it cancelled to leave the scheduler.
#: Slurm's default ``KillWait`` — between a cancelled job's SIGTERM and its
#: SIGKILL — is 30 s, and the pipeline answers SIGTERM by winding down.
CANCEL_SETTLE_S = 45


def sweep_orphaned_allocation(
    host: RemoteHost, remote_pidfile: str, heartbeat: str,
    *, log_fn: Callable[[str], None] | None = None,
) -> str:
    """Cancel an allocation a *previous* run of this app left behind.

    The scancel backstop covers every exit the app survives: a clean quit,
    a finished run, a Stop click. It cannot cover the ones where no Python
    of ours runs at all — SIGKILL, a segfault, a force-quit, the power
    going — because it needs the job id held in memory. The id is on the
    shared filesystem the whole time (`<pidfile>.slurm`), and ``run_id`` is
    deterministic in the input and the arguments, so the next run of the
    same plate can find exactly the job the crashed one left.

    **Guarded, because that determinism cuts both ways.** Two instances
    running the same plate compute the same ``run_id``, so an unconditional
    sweep would have each cancel the other's live job. Only a job the
    scheduler still lists as alive is anybody's (``squeue``; a host without it
    falls back to the ages below). Of those, one that is still owned is
    beating its heartbeat, and one that started moments ago has a fresh
    sidecar; either is left alone. What gets cancelled is a live job whose
    owner has been silent for longer than a stalled upload would be — and the
    sweep then waits, bounded by :data:`CANCEL_SETTLE_S`, for the scheduler to
    stop listing it, because a cancelled pipeline winds down for up to
    Slurm's ``KillWait`` and the caller is about to write where it wrote.

    Every step that cannot answer refuses to cancel rather than guessing.
    A job that is somebody else's, one whose age this host's ``stat``
    cannot report, or one ``squeue`` could not be asked about, is left
    running: the cost of not sweeping is an idle node until its walltime,
    and the cost of sweeping wrongly is somebody's half-finished plate.

    Returns which of those it found — ``"swept"``, ``"owned"``,
    ``"notmine"``, ``"unknown"`` or ``"none"`` (no sidecar, or its job has
    ended) — because the caller has to know whether the run directory it is
    about to empty currently belongs to somebody. A job that was cancelled
    and is still shutting down at the deadline is ``"owned"``: something is
    still writing there.
    """
    return sweep_allocations(
        host, [(remote_pidfile, heartbeat, "this dataset's run directory")],
        log_fn=log_fn)[0]


def _target_script(index: int, pidfile: str, heartbeat: str) -> str:
    """The sweep of one run directory, as a subshell printing one verdict line,
    ``AW_<VERDICT> <index> <job id> [detail]`` — or nothing, for no sidecar."""
    sidecar_q = _remote_quote(f"{pidfile}.slurm")
    hb_q = _remote_quote(heartbeat)
    stale = UPLOAD_HEARTBEAT_STALE_S
    ended = "|".join(_ENDED_STATES)
    state = "awk 'NF {print $1; exit}'"
    return (
        f'( _jid=$(cat {sidecar_q} 2>/dev/null); '
        f'[ -z "$_jid" ] && exit 0; '
        # Alive at all? Asked before anything is read off a file's age. A
        # purged job is "Invalid job id" with exit 1; any other failure is a
        # scheduler that could not be asked, which proves nothing either way.
        f'_aw_sq=0; if command -v squeue >/dev/null 2>&1; then _aw_sq=1; '
        f'_st=$(squeue -h -j "$_jid" -o %T 2>&1); '
        f'if [ $? -ne 0 ]; then case "$_st" in *"nvalid job id"*) _st="";; '
        f'*) echo "AW_UNKNOWN {index} $_jid squeue"; exit 0;; esac; fi; '
        f"_st=$(printf '%s\\n' \"$_st\" | {state}); "
        f'case "$_st" in ""|{ended}) rm -f {sidecar_q}; '
        f'echo "AW_GONE {index} $_jid"; exit 0;; esac; fi; '
        # Ages are computed remotely so no clock is compared against another
        # machine's, and both spellings of ``stat`` are tried — GNU's ``-c %Y``
        # and BSD's ``-f %m`` — because a host with only the second used to
        # report every age as zero, which reads as "long dead".
        f'_now=$(date +%s); '
        f'_sc=$(stat -c %Y {sidecar_q} 2>/dev/null || '
        f'stat -f %m {sidecar_q} 2>/dev/null || echo ""); '
        f'if [ -z "$_sc" ]; then echo "AW_UNKNOWN {index} $_jid stat"; exit 0; fi; '
        f'_hb=0; if [ -e {hb_q} ]; then '
        f'_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
        f'stat -f %m {hb_q} 2>/dev/null || echo 0); fi; '
        f'if [ "$_hb" -ne 0 ] && [ "$((_now - $_hb))" -le {stale} ]; then '
        f'echo "AW_OWNED {index} $_jid"; exit 0; fi; '
        f'if [ "$((_now - $_sc))" -le {stale} ]; then '
        f'echo "AW_OWNED {index} $_jid"; exit 0; fi; '
        + _only_my_job("_jid", f"AW_NOTMINE {index}") +
        'command -v scancel >/dev/null 2>&1 '
        '&& scancel -u "$_aw_me" "$_jid" 2>/dev/null || exit 0; '
        # Cancelled: wait for it to be gone before the caller writes where it
        # wrote, then forget it. Still listed at the deadline: say so.
        f'_w=0; while [ "$_aw_sq" = 1 ] && [ "$_w" -lt {CANCEL_SETTLE_S} ]; do '
        f'_st=$(squeue -h -j "$_jid" -o %T 2>/dev/null | {state}); '
        f'case "$_st" in ""|{ended}) break;; esac; sleep 1; _w=$((_w+1)); done; '
        f'if [ "$_w" -ge {CANCEL_SETTLE_S} ]; then echo "AW_DYING {index} $_jid"; exit 0; fi; '
        f'rm -f {sidecar_q} {hb_q}; echo "AW_SWEPT {index} $_jid" )'
    )


def sweep_allocations(
    host: RemoteHost, targets: Iterable[tuple[str, str, str]],
    *, log_fn: Callable[[str], None] | None = None,
) -> list[str]:
    """:func:`sweep_orphaned_allocation` over several run directories in one
    ssh. *targets* holds ``(pidfile, heartbeat, where)``, *where* naming the
    directory in the log. Returns one verdict per target, in order."""
    targets = list(targets)
    log = log_fn or (lambda _m: None)
    if not host.compute_launcher.strip() or not targets:
        return ["none"] * len(targets)
    script = "; ".join(_target_script(i, pidfile, hb)
                       for i, (pidfile, hb, _where) in enumerate(targets))
    try:
        result = subprocess.run(
            host.ssh_argv(allocate_tty=False) + [script],
            capture_output=True, timeout=30 + CANCEL_SETTLE_S, **TEXT_UTF8,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"[remote] WARN scheduler: could not check for a leftover "
            f"allocation ({exc}).\n")
        return ["unknown"] * len(targets)
    found: dict[int, list[str]] = {}
    for line in (result.stdout or "").splitlines():
        words = line.split()
        if len(words) >= 3 and words[0].startswith("AW_") and words[1].isdigit():
            found.setdefault(int(words[1]), [words[0][3:].lower(), *words[2:]])
    return [_verdict(found.get(i), where, log)
            for i, (_pidfile, _hb, where) in enumerate(targets)]


def _verdict(found: list[str] | None, where: str, log: Callable[[str], None]) -> str:
    """Log what the sweep of one directory found, and return its verdict."""
    if not found or found[0] == "gone":
        return "none"
    kind, jid, rest = found[0], found[1], found[2:]
    if kind == "swept":
        log(f"[remote] scheduler: cancelled a job left over from an earlier run of this "
            f"dataset ({jid}, in {where}) — the app it belonged to is gone.\n")
        return "swept"
    if kind == "dying":
        log(f"[remote] WARN scheduler: cancelled job {jid} in {where}, left over from an "
            f"earlier run of this dataset, and it was still shutting down "
            f"{CANCEL_SETTLE_S} s later.\n")
        return "owned"
    if kind == "notmine":
        log(f"[remote] scheduler: the job in {where} ({jid}) belongs to "
            f"{rest[0] if rest else 'another user'} — not touching it.\n")
        return "notmine"
    if kind == "owned":
        log(f"[remote] scheduler: another session is running this same dataset "
            f"(job {jid}, in {where}); leaving it alone.\n")
        return "owned"
    if rest[:1] == ["squeue"]:
        log(f"[remote] WARN scheduler: squeue could not say whether job {jid} in {where} "
            f"is still running, so it is left alone.\n")
    else:
        log(f"[remote] WARN scheduler: this host cannot report a file's modification "
            f"time, so there is no way to tell whether job {jid} in {where} is still in "
            f"use. Leaving it running.\n")
    return "unknown"
