"""A granted node kept between runs: the job that keeps it, and the script that job runs.

Every remote run on a scheduler-backed host used to ask for a node of its own and give it back when it ended, so
a run that failed or was stopped after its node was granted threw the node away and the next run queued again.
Manage Remotes' **Request a node** asks the scheduler for a node once and keeps it, and every remote run on the host
then runs inside it as a job step (see ``GRANTED_NODE_TRACKER.md`` for the whole design).

The job that keeps the node is the **hold job**: a Slurm batch job (``sbatch``), not an ``srun`` client, because a
batch allocation lives until its script exits, it is cancelled, or ``--time`` runs out, whereas an ``srun`` started
from a login shell dies with its ssh session and takes its allocation with it. The hold job's script
(:func:`compose_hold_script`) does nothing but watch, and it is the only thing that ends the node on its own:

* **Release** was asked for (the app wrote ``release``): it ends.
* **The app is gone** (its heartbeat has been silent for ``UPLOAD_HEARTBEAT_STALE_S``) **and no run is using the
  node**: it ends. A node therefore never outlives the app that asked for it by more than that, whether the app
  quit, crashed, slept or lost its network.
* **The node has been idle** for the host's idle limit: it ends. ``0`` turns this rule off, and only this one.
* **Slurm ends it** at ``--time``, whatever is running; the script says why it was ended.

The script runs *on the node*, so none of these depends on the app being alive to act. What it writes, and what
the app writes for it, are the files named in :class:`HoldPaths`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from services.remote_config import RemoteHost
from services.remote_srun_retry import ALLOCATION_GRANTED
from services.remote_transport import _remote_quote
from services.remote_wait_snippets import (
    MID_BATCH_HEARTBEAT_STALE_S,
    UPLOAD_HEARTBEAT_STALE_S,
    _heartbeat_stale_snippet,
)

#: Where holds live, under the host's staging directory.
HOLDS_DIRNAME = "holds"
#: How often the hold script looks at its files, in seconds.
HOLD_POLL_S = 15
#: A SIGTERM this close to the end of the time limit (or later) is Slurm ending the job at ``--time``; an earlier
#: one is a ``scancel``. Slurm signals a little before it kills, so the script cannot ask for exact equality.
TIME_LIMIT_SLACK_S = 60


@dataclass(frozen=True)
class HoldPaths:
    """The files one hold owns, all in one directory of their own."""

    dir: str
    #: The hold's Slurm job id; the app writes it after ``sbatch``.
    job: str
    #: ``node=``, ``gpus=``, ``cpus=``, ``granted_at=`` (epoch), ``limit_s=``, ``job=``; the script writes it once,
    #: when the node is granted.
    granted: str
    #: The app's last sign of life: its mtime, touched every ``UPLOAD_HEARTBEAT_S``.
    heartbeat: str
    #: Present while a run is using, or preparing to use, the node; the app creates it and its run heartbeat
    #: keeps it fresh.
    busy: str
    #: ``state=busy|idle`` and ``idle_s=<seconds>``, rewritten each poll, so the app can show how long the node
    #: has been idle without comparing its own clock with the node's.
    status: str
    #: Its presence asks the script to end the hold now.
    release: str
    #: One line saying why the hold ended; the script writes it on the way out.
    ended: str
    #: The batch job's own output (``sbatch -o``).
    log: str
    #: The script itself.
    script: str


def host_key(host: RemoteHost) -> str:
    """A short, filesystem-safe name for *host*'s hold directory: one granted node per host per user."""
    return hashlib.sha1(f"{host.name}\0{host.ssh_alias}".encode()).hexdigest()[:12]


def hold_paths(host: RemoteHost) -> HoldPaths:
    """``{remote_staging_dir}/holds/<host-key>/…``."""
    root = f"{host.remote_staging_dir}/{HOLDS_DIRNAME}/{host_key(host)}"
    return HoldPaths(dir=root, job=f"{root}/job", granted=f"{root}/granted", heartbeat=f"{root}/heartbeat",
                     busy=f"{root}/busy", status=f"{root}/status", release=f"{root}/release",
                     ended=f"{root}/ended", log=f"{root}/log", script=f"{root}/hold.sh")


def _duration_words(seconds: int) -> str:
    """``30 min``, ``2 h``, ``45 s``: the whole unit that *seconds* is a multiple of."""
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def compose_hold_script(paths: HoldPaths, *, limit_s: int, idle_release_s: int, poll_s: int = HOLD_POLL_S,
                        heartbeat_stale_s: int = UPLOAD_HEARTBEAT_STALE_S,
                        busy_stale_s: int = MID_BATCH_HEARTBEAT_STALE_S) -> str:
    """The POSIX ``sh`` script the hold job runs on its node.

    *limit_s* is the job's ``--time`` in seconds (it tells a time-limit SIGTERM from a ``scancel``),
    *idle_release_s* the idle limit (``0`` = never release for idleness), and the other three are the loop's
    interval and the two staleness limits. They are parameters so a test can shorten them; the app passes the
    defaults.

    The heartbeat check is :func:`services.remote_wait_snippets._heartbeat_stale_snippet`, the one the upload wait
    and the batch driver already use: it reads "now" from the file server that stamped the heartbeat rather than
    from this node's clock, copes with a ``stat`` that is not GNU's, and tolerates a file that has not appeared
    yet. It is run twice on different files (the app's heartbeat, and a run's ``busy`` marker), which share its
    ``_aw_miss`` counter; they never run in the same poll for a file that is missing, so the counter is only ever
    counting one of them.

    The idle clock and the elapsed time are both measured with this node's ``date`` against this node's ``date``,
    never against a timestamp another machine wrote.
    """
    q = {name: _remote_quote(getattr(paths, name)) for name in HoldPaths.__dataclass_fields__}
    idle_label = _duration_words(idle_release_s) if idle_release_s else ""
    limit_label = _duration_words(limit_s)

    # The app gone: the message the check prints, and what it does. `_aw_end` writes the reason and exits.
    _, gone_check = _heartbeat_stale_snippet(
        paths.heartbeat,
        f"[remote] granted node: All-Well stopped answering ({_duration_words(heartbeat_stale_s)} without a "
        "heartbeat) and no run is using the node",
        stale_s=heartbeat_stale_s, poll_s=poll_s,
        on_stale=f"_aw_end 'All-Well stopped answering ({_duration_words(heartbeat_stale_s)} without a heartbeat) "
                 "and no run was using it'")
    # A run's marker that its own heartbeat has stopped refreshing is a run whose app died mid-run: not busy.
    # The check prints each time it judges, which would be a line every poll, so its output is dropped.
    _, busy_check = _heartbeat_stale_snippet(
        paths.busy, "", stale_s=busy_stale_s, poll_s=poll_s, on_stale="_aw_busy=0")
    init, _ = _heartbeat_stale_snippet(paths.heartbeat, "", stale_s=heartbeat_stale_s, poll_s=poll_s)

    return f'''#!/bin/sh
# The hold job: keeps this node granted and ends it when the rules in services/remote_hold.py say so.
_aw_start=$(date +%s)
_aw_last_used=$_aw_start
_aw_node="${{SLURMD_NODENAME:-$(hostname)}}"

# Written whole and renamed, so the app never reads half a line.
_aw_write() {{ printf '%s\\n' "$2" > "$1.tmp" && mv "$1.tmp" "$1"; }}

_aw_end() {{
    _aw_write {q["ended"]} "$1"
    echo "[remote] granted node $_aw_node (job ${{SLURM_JOB_ID:-?}}) released: $1."
    exit 0
}}

# Slurm's own end of the job: SIGTERM at --time, or from a scancel (the app's Release/quit, or anyone's).
_aw_term() {{
    _aw_elapsed=$(( $(date +%s) - _aw_start ))
    if [ "$_aw_elapsed" -ge {max(0, limit_s - TIME_LIMIT_SLACK_S)} ]; then
        _aw_end "reached its {limit_label} time limit"
    fi
    _aw_end "cancelled"
}}
trap _aw_term TERM INT HUP

_aw_gpus=$(nvidia-smi -L 2>/dev/null | sed -e 's/^GPU [0-9]*: //' -e 's/ (UUID.*$//' | paste -sd, - 2>/dev/null)
_aw_write {q["granted"]} "node=$_aw_node
gpus=$_aw_gpus
cpus=${{SLURM_CPUS_ON_NODE:-}}
granted_at=$_aw_start
limit_s={limit_s}
job=${{SLURM_JOB_ID:-}}"
_aw_write {q["status"]} "state=idle
idle_s=0"
echo "{ALLOCATION_GRANTED} — job ${{SLURM_JOB_ID:-?}} on $_aw_node (partition ${{SLURM_JOB_PARTITION:-?}}); kept until released, idle {idle_label or "never limited"}, or {limit_label}"

{init}
while :; do
    if [ -e {q["release"]} ]; then _aw_end "released on request"; fi
    _aw_busy=0
    if [ -e {q["busy"]} ]; then
        _aw_busy=1
        {{ {busy_check} }} >/dev/null 2>&1
    fi
    _aw_now=$(date +%s)
    if [ "$_aw_busy" = 1 ]; then
        _aw_last_used=$_aw_now
        _aw_write {q["status"]} "state=busy
idle_s=0"
    else
        {gone_check}
        _aw_idle=$(( _aw_now - _aw_last_used ))
        {f'if [ "$_aw_idle" -ge {idle_release_s} ]; then _aw_end "idle {idle_label}"; fi' if idle_release_s else ":"}
        _aw_write {q["status"]} "state=idle
idle_s=$_aw_idle"
    fi
    # `sleep` in the background and `wait` for it, so a SIGTERM is answered at once, not after the nap.
    sleep {poll_s} &
    wait $!
done
'''
