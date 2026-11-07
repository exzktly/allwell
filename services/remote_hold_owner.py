"""Who holds a run's claim on a granted node, and taking over one whose All-Well is gone.

A run claims the granted node with the hold directory's ``busy`` marker (``services.remote_hold.claim_script``), kept
fresh by the run's heartbeat. When the app crashes the heartbeat stops, and the marker counts as abandoned only once
it is ``MID_BATCH_HEARTBEAT_STALE_S`` old (ten minutes). Until then every run — including the one the user starts the
moment the app is open again — read the node as "busy with another run" and waited, and Manage Remotes said the node
was in use by a run that no longer existed.

So the marker names its owner: ``<pid>@<host> <token>``, where *token* is random per process. A claim whose owner is
on this computer and is not running is **orphaned** (:func:`orphaned_by`): the window that made it has crashed, and
nobody else can be refreshing it. A run that finds one takes it over at once (:func:`takeover_script`) — unless a
step of the hold job is still running, because then the crashed window's pipeline is still on the node and a second
run beside it is the out-of-memory failure the claim exists to prevent. Then the run waits, and says what for.

An owner on another computer cannot be checked from here and is always treated as running, as is a marker with no
readable owner (one written before markers named theirs): both fall back to the ten-minute rule.
"""

from __future__ import annotations

import os
import secrets
import socket

from core.process import pid_alive
from services.remote_transport import _remote_quote

#: Random per process, so a restarted app that happens to get its crashed predecessor's process id is not mistaken
#: for it.
_TOKEN = secrets.token_hex(6)


def _host() -> str:
    return socket.gethostname() or "?"


def claim_owner() -> str:
    """This process as a claim names it: ``<pid>@<host> <token>``."""
    return f"{os.getpid()}@{_host()} {_TOKEN}"


def orphaned_by(holder: str) -> int | None:
    """The process id of the gone All-Well that left the claim *holder*, or ``None`` when the claim may be live.

    A claim is orphaned only when it names a process on this computer that is not running, or this very process id
    with a different token (the app was restarted and given its old id). Anything else — another computer, this
    process's own claim, a marker with no owner or one that does not parse — is ``None``."""
    first = (holder or "").strip().splitlines()[0] if (holder or "").strip() else ""
    who, _, token = first.partition(" ")
    pid_text, at, host = who.partition("@")
    if not (pid_text.isdigit() and at and host and token):
        return None
    if host != _host():
        return None
    pid = int(pid_text)
    if pid == os.getpid():
        return pid if token != _TOKEN else None
    return None if pid_alive(pid) else pid


def takeover_script(busy: str, holder: str, owner: str, job_id: str) -> str:
    """Shell that replaces the orphaned claim *holder* in the marker *busy* with this run's (*owner*).

    Prints ``AW_LEASED`` when it did; ``AW_STEP <step>`` when a step of the hold job *job_id* is still running (the
    crashed window's run is still on the node, so it is not taken); ``AW_UNKNOWN`` when the scheduler could not say
    whether one is; ``AW_CHANGED`` when the marker no longer holds *holder* (its owner came back, or another window
    took it first) — the caller then claims as usual.

    The marker is moved aside before it is replaced, and ``mv`` of one file can succeed for one caller only, so two
    windows taking over the same orphan cannot both win. A marker that changed between the check and the move is put
    back (``mv -n`` does not overwrite a claim made since)."""
    b = _remote_quote(busy)
    want = _remote_quote(holder.strip())
    me = _remote_quote(owner)
    step_check = ""
    if job_id:
        j = _remote_quote(job_id)
        step_check = (
            "command -v squeue >/dev/null 2>&1 || { echo AW_UNKNOWN; exit 0; }\n"
            f"_aw_steps=$(squeue -h --steps --jobs={j} -o %i 2>/dev/null) || {{ echo AW_UNKNOWN; exit 0; }}\n"
            f"_aw_step=$(printf '%s\\n' \"$_aw_steps\" | grep -E \"^{job_id}\\.[0-9]+$\" | head -n1)\n"
            '[ -n "$_aw_step" ] && { echo "AW_STEP $_aw_step"; exit 0; }\n')
    return (f'[ "$(cat {b} 2>/dev/null)" = {want} ] || {{ echo AW_CHANGED; exit 0; }}\n'
            + step_check +
            f'_aw_aside={b}.orphan.$$\n'
            f'mv -f {b} "$_aw_aside" 2>/dev/null || {{ echo AW_CHANGED; exit 0; }}\n'
            f'if [ "$(cat "$_aw_aside" 2>/dev/null)" != {want} ]; then\n'
            f'    mv -n "$_aw_aside" {b} 2>/dev/null; rm -f "$_aw_aside"; echo AW_CHANGED; exit 0\n'
            'fi\n'
            'rm -f "$_aw_aside"\n'
            f"if ( set -C; printf '%s\\n' {me} > {b} ) 2>/dev/null; then echo AW_LEASED; else echo AW_CHANGED; fi\n"
            "exit 0\n")
