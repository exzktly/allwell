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

import posixpath
import subprocess
from collections.abc import Callable, Iterable

from services import run_journal
from services.remote_config import RemoteHost
from services.remote_jobs import _only_my_job
from services.remote_run_id import (
    LEGACY_UPLOAD_HEARTBEAT,
    UPLOAD_HEARTBEAT,
    _local_input_dir_from_argv,
    legacy_argv_digests,
    pipeline_argv_digest,
    remote_run_dir,
)
from services.remote_scratch import source_fingerprint
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
    legacy_q = _remote_quote(f"{posixpath.dirname(pidfile)}/{LEGACY_UPLOAD_HEARTBEAT}")
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
        # The newest of the heartbeat and where an older app beats it, so a
        # session still running an older app counts as this directory's owner.
        f'_hb=0; for _aw_f in {hb_q} {legacy_q}; do if [ -e "$_aw_f" ]; then '
        f'_aw_t=$(stat -c %Y "$_aw_f" 2>/dev/null || '
        f'stat -f %m "$_aw_f" 2>/dev/null || echo 0); '
        f'[ "$_aw_t" -gt "$_hb" ] && _hb=$_aw_t; fi; done; '
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
    log = log_fn or (lambda _m: None)
    return [_verdict(found, where, log) for found, where in _sweep(host, targets, log)]


def _sweep(host: RemoteHost, targets, log: Callable[[str], None]) -> list[tuple[list[str], str]]:
    """Run the sweep of every target in one ssh; ``(what it printed, where)`` per
    target, the first being ``[verdict, job id, detail…]`` or empty."""
    targets = list(targets)
    if not host.compute_launcher.strip() or not targets:
        return [([], where) for _p, _h, where in targets]
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
        return [(["unknown", "?", "ssh"], where) for _p, _h, where in targets]
    found: dict[int, list[str]] = {}
    for line in (result.stdout or "").splitlines():
        words = line.split()
        if len(words) >= 3 and words[0].startswith("AW_") and words[1].isdigit():
            found.setdefault(int(words[1]), [words[0][3:].lower(), *words[2:]])
    return [(found.get(i, []), where) for i, (_p, _h, where) in enumerate(targets)]


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
    elif rest[:1] == ["stat"]:
        log(f"[remote] WARN scheduler: this host cannot report a file's modification "
            f"time, so there is no way to tell whether job {jid} in {where} is still in "
            f"use. Leaving it running.\n")
    return "unknown"


# ── claiming the directories a run is about to use (review E1, E2) ──────────

#: What the sweep of a directory may find that refuses the run: a job that may
#: still be writing there, and that this app may not, or cannot, stop.
_REFUSING = ("owned", "dying", "notmine", "unknown")


def earlier_attempt_dirs(host: RemoteHost, local_output_dir, pipeline_argv,
                         *, extras=(), own_run_dir: str) -> list[str]:
    """The run directory of every earlier attempt of this plate the journal
    recorded under these settings, source images and host — other than
    *own_run_dir*.

    A resume never shares its run directory with the attempt it resumes (its
    staged archives, and so its ``run_id``, differ), but it writes into that
    attempt's output directory and takes its archives, so a job still alive
    there is a second pipeline writing the same output. Both the recorded
    ``run_id``'s directory and the one holding the recorded output are listed,
    since a re-attached attempt wrote its output under an attempt before it.
    Empty on a host with no scheduler, and on a journal that cannot be read.
    """
    if not host.compute_launcher.strip() or local_output_dir is None:
        return []
    argv = list(pipeline_argv)
    try:
        attempts = run_journal.recorded_remote_attempts(
            local_output_dir,
            argv_digests=[pipeline_argv_digest(argv, extras=extras),
                          *legacy_argv_digests(argv, extras=extras)],
            source_key=source_fingerprint(_local_input_dir_from_argv(argv)),
            host_alias=host.ssh_alias)
    except OSError:
        return []
    dirs: list[str] = []
    for attempt in attempts:
        out = str(attempt.get("remote_output_dir") or "")
        rid = str(attempt.get("run_id") or "")
        for d in (remote_run_dir(host, rid) if rid else "",
                  out[:-len("/out")] if out.endswith("/out") else ""):
            if d and d != own_run_dir and d not in dirs:
                dirs.append(d)
    return dirs


def claim_run_dirs(host: RemoteHost, own_pidfile: str, own_heartbeat: str,
                   earlier: Iterable[str] = (), *,
                   log_fn: Callable[[str], None] | None = None) -> str:
    """Sweep this run's own directory and every *earlier* attempt's; return
    ``""`` when the run may go ahead, or the sentence refusing it.

    An earlier attempt's job that no app is feeding any more is the same
    user's own abandoned attempt at this plate: it is cancelled, and the log
    names the directory it was found in (:func:`sweep_orphaned_allocation`).
    One that is still being fed, one that belongs to someone else, one still
    shutting down after its cancel, or one whose state cannot be learned
    refuses the run instead: cancelling a fed job is cancelling another
    window's run, and letting both go on would have two pipelines write one
    output.
    """
    log = log_fn or (lambda _m: None)
    targets = [(own_pidfile, own_heartbeat, "this dataset's run directory")] + [
        (f"{d}/run.pid", f"{d}/{UPLOAD_HEARTBEAT}", f"an earlier attempt's run directory, {d}")
        for d in earlier]
    for found, where in _sweep(host, targets, log):
        _verdict(found, where, log)
        if found[:1] and found[0] in _REFUSING:
            return _refusal(found[0], found[1:], where)
    return ""


def _refusal(kind: str, found: list[str], where: str) -> str:
    """Why a run stops before touching a directory a job may still be writing."""
    jid = found[0] if found else "?"
    yourself = (f"If no other All-Well window is running this plate, cancel it yourself "
                f"(scancel {jid}) and start the run again.")
    if kind == "owned":
        return (f"another session is already running this exact dataset and configuration "
                f"on this host (job {jid}, in {where}, whose app is still beating its "
                f"heartbeat). Both would write the same remote output, so this run stops "
                f"here rather than corrupting the one already going. A run stopped in the "
                f"last {UPLOAD_HEARTBEAT_STALE_S // 60} minutes whose cancel did not reach the "
                f"cluster looks the same: wait that long and start again. " + yourself)
    if kind == "dying":
        return (f"job {jid}, in {where}, was cancelled and is still shutting down; this run "
                f"would write where it writes, so it stops here. Start it again in a minute.")
    if kind == "notmine":
        owner = found[1] if len(found) > 1 else "another user"
        return (f"job {jid}, in {where}, belongs to {owner} and is still running there; this "
                f"run would write where it writes, so it stops here. Ask {owner} to stop it.")
    return (f"could not tell whether job {jid}, in {where}, is still running, so this run "
            f"stops rather than risk two pipelines writing one output. " + yourself)
