"""The next run's sweep of an allocation an earlier run of the same plate left.

:func:`services.remote_jobs.close_remote_job` covers every exit the app
survives. This covers the ones it does not — a SIGKILL, a segfault, a power
cut — by reading the job id the crashed run left on the shared filesystem
(``<pidfile>.slurm``) and cancelling it when nobody owns it any more.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from services.remote_config import RemoteHost
from services.remote_jobs import _only_my_job
from services.remote_transport import _remote_quote
from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S
from services.subprocess_text import TEXT_UTF8


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
    sweep would have each cancel the other's live job. A run that is still
    owned is beating its heartbeat, and one that started moments ago has a
    fresh sidecar; either is left alone. What gets cancelled is a job whose
    owner has been silent for longer than a stalled upload would be.

    Every step that cannot answer refuses to cancel rather than guessing.
    A job that is somebody else's, or one whose age this host's ``stat``
    cannot report, is left running: the cost of not sweeping is an idle
    node until its walltime, and the cost of sweeping wrongly is somebody's
    half-finished plate.

    Returns which of those it found — ``"swept"``, ``"owned"``,
    ``"notmine"``, ``"unknown"`` or ``"none"`` — because the batched path
    also has to know whether the run directory it is about to empty
    currently belongs to somebody.
    """
    if not host.compute_launcher.strip():
        return "none"
    log = log_fn or (lambda _m: None)
    sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
    hb_q = _remote_quote(heartbeat)
    stale = UPLOAD_HEARTBEAT_STALE_S
    # One round trip: read the id, and the two ages that decide whether it
    # is anyone's. Ages are computed remotely so no clock is compared
    # against another machine's, and both spellings of ``stat`` are tried
    # — GNU's ``-c %Y`` and BSD's ``-f %m`` — because a host with only the
    # second used to report every age as zero, which reads as "long dead"
    # and would cancel a job that had started seconds earlier.
    script = (
        f'_jid=$(cat {sidecar_q} 2>/dev/null); '
        f'[ -z "$_jid" ] && exit 0; '
        f'_now=$(date +%s); '
        f'_sc=$(stat -c %Y {sidecar_q} 2>/dev/null || '
        f'stat -f %m {sidecar_q} 2>/dev/null || echo ""); '
        f'if [ -z "$_sc" ]; then echo "AW_UNKNOWN $_jid"; exit 0; fi; '
        f'_hb=0; if [ -e {hb_q} ]; then '
        f'_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
        f'stat -f %m {hb_q} 2>/dev/null || echo 0); fi; '
        f'if [ "$_hb" -ne 0 ] && '
        f'[ "$((_now - $_hb))" -le {stale} ]; then '
        f'echo "AW_OWNED $_jid"; exit 0; fi; '
        f'if [ "$((_now - $_sc))" -le {stale} ]; then '
        f'echo "AW_OWNED $_jid"; exit 0; fi; '
        + _only_my_job("_jid", "AW_NOTMINE") +
        'command -v scancel >/dev/null 2>&1 '
        '&& scancel -u "$_aw_me" "$_jid" '
        '2>/dev/null && echo "AW_SWEPT $_jid" || true'
    )
    try:
        result = subprocess.run(
            host.ssh_argv(allocate_tty=False) + [script],
            capture_output=True, timeout=15, **TEXT_UTF8,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"[remote] WARN scheduler: could not check for a leftover "
            f"allocation ({exc}).\n")
        return "unknown"
    out = result.stdout or ""
    if "AW_SWEPT" in out:
        jid = out.split("AW_SWEPT", 1)[1].strip().split()[:1]
        log(f"[remote] scheduler: cancelled a job left over from an "
            f"earlier run of this dataset ({jid[0] if jid else '?'}) — "
            f"the app it belonged to is gone.\n")
        return "swept"
    if "AW_NOTMINE" in out:
        rest = out.split("AW_NOTMINE", 1)[1].strip().split()
        log(f"[remote] scheduler: the job in this dataset's run directory "
            f"({rest[0] if rest else '?'}) belongs to "
            f"{rest[1] if len(rest) > 1 else 'another user'} — not touching "
            f"it.\n")
        return "notmine"
    if "AW_OWNED" in out:
        jid = out.split("AW_OWNED", 1)[1].strip().split()[:1]
        log(f"[remote] scheduler: another session is running this same "
            f"dataset (job {jid[0] if jid else '?'}); leaving it alone.\n")
        return "owned"
    if "AW_UNKNOWN" in out:
        jid = out.split("AW_UNKNOWN", 1)[1].strip().split()[:1]
        log(f"[remote] WARN scheduler: this host cannot report a file's "
            f"modification time, so there is no way to tell whether job "
            f"{jid[0] if jid else '?'} in this dataset's run directory is "
            f"still in use. Leaving it running.\n")
        return "unknown"
    return "none"
