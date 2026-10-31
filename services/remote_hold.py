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
import re
import shlex
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from services.remote_config import RemoteHost, parse_slurm_time, render_slurm_time
from services.remote_heartbeat import _UploadHeartbeat
from services.remote_jobs import _only_my_job
from services.slurm_ids import HOLDS_DIRNAME
from services.remote_srun_retry import (
    ALLOCATION_GRANTED,
    BOOT_RETRY_PAUSE_S,
    _split_launcher,
    _take_option,
    is_srun,
    one_task,
    queue_wait_seconds,
)
from services.remote_transport import _remote_join, _remote_quote
from services.remote_wait_snippets import (
    MID_BATCH_HEARTBEAT_STALE_S,
    UPLOAD_HEARTBEAT_STALE_S,
    _heartbeat_stale_snippet,
)

#: How often the hold script looks at its files, in seconds.
HOLD_POLL_S = 15
if TYPE_CHECKING:
    from services.remote_upload import DatasetCache

#: What the hold job is called in the scheduler's queue.
HOLD_JOB_NAME = "allwell-hold"
#: How long one control ssh may take (the scripts below are a few small commands).
CONTROL_TIMEOUT_S = 60
#: ``scontrol`` job states in which the job has ended and will not run the hold script (again).
_TERMINAL_STATES = ("COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "NODE_FAIL", "BOOT_FAIL", "PREEMPTED",
                    "DEADLINE", "OUT_OF_MEMORY", "SPECIAL_EXIT", "REVOKED")
#: States that mean the node the job was given did not come up (compare ``remote_srun_retry._BOOT_FAILURE_RE``).
_BOOT_FAILED_STATES = ("BOOT_FAIL", "NODE_FAIL")
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
    if [ -e {q["release"]} ]; then _aw_end "released on request"; fi
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


# ── asking for the node ──────────────────────────────────────────────────────────────────────────────────────────

class HoldError(RuntimeError):
    """A granted node could not be requested, or was lost while it was being requested. The message is shown to the
    user as it stands, so it names the cause."""


class HoldQueueWaitSpent(HoldError):
    """The scheduler did not grant the node within the host's Max queue wait; the request was cancelled."""


def sbatch_options(launcher: str) -> tuple[list[str], list[str], str]:
    """``(options, the launcher's own --exclude nodes, partition)`` for an ``sbatch`` that asks for what the
    host's scheduler command (an ``srun`` line) asks for.

    One node, one task (:func:`~services.remote_srun_retry.one_task`: a ``--ntasks`` is folded into
    ``--cpus-per-task`` as it is for a run). Dropped, because they describe a run's ``srun`` and not a hold: the
    program word, any ``--time`` (the hold has its own, :func:`hold_time_limit_s`), and the output and terminal
    options. Everything else — partition, account, GPUs, memory, ``--exclusive`` and options this code has never
    heard of — is kept as written, so what the cluster accepts for a run it accepts for the hold; an option
    ``sbatch`` refuses is shown to the user with ``sbatch``'s own words.
    """
    if not is_srun(launcher):
        raise HoldError("Request a node needs a Slurm scheduler command that starts with srun.")
    tokens = [t for t in shlex.split(launcher)[1:] if t != "--pty"]
    tokens = one_task(tokens)
    for short, long in (("-t", "--time"), ("-o", "--output"), ("-e", "--error")):
        tokens, _ = _take_option(tokens, short, long)
    kept, excluded, partition = _split_launcher(tokens)
    return kept, excluded, partition


def parse_partition_limit(text: str) -> int | None:
    """Seconds in a partition's time limit as ``sinfo -o %l`` prints it, ``None`` when it has none (``infinite``,
    ``UNLIMITED``) or when the text is not a time."""
    word = text.strip()
    if not word or word.lower() in ("infinite", "unlimited", "n/a"):
        return None
    return parse_slurm_time(word)


def partition_limit_script(partition: str) -> str:
    """Shell printing the time limit of *partition* (or, empty, the cluster's default partition) as ``sinfo`` has
    it. Prints nothing when ``sinfo`` is missing."""
    if partition:
        return f"sinfo -h -p {shlex.quote(partition)} -o %l 2>/dev/null | head -n1\n"
    return "sinfo -h -o '%P %l' 2>/dev/null | awk '$1 ~ /\\*$/ {print $2; exit}'\n"


def hold_time_limit_s(host: RemoteHost, partition_cap_s: int | None) -> int:
    """The hold job's ``--time`` in seconds: the host's setting, lowered to the partition's limit when that is
    shorter."""
    wanted = int(host.granted_time_limit_h) * 3600
    return min(wanted, partition_cap_s) if partition_cap_s else wanted


def prepare_script(paths: HoldPaths) -> str:
    """Shell that makes the hold directory and clears what an earlier hold left in it. Only ever run when no live
    hold exists, so nothing here belongs to a running one."""
    d = _remote_quote(paths.dir)
    gone = " ".join(_remote_quote(f) for f in (paths.job, paths.granted, paths.busy, paths.status, paths.release,
                                               paths.ended, paths.log, paths.script))
    return f"mkdir -p {d} && rm -f {gone}\n"


def submit_script(paths: HoldPaths, script_text: str, options: list[str], *, limit_s: int) -> str:
    """Shell that writes the hold script, submits it with ``sbatch`` and records the job id.

    Prints the id on success. ``sbatch``'s own error goes to stderr and its exit code is the script's, so the
    caller can show the scheduler's words. The script text is passed through a quoted here-document, so nothing in
    it is expanded here.
    """
    marker = "AW_HOLD_SCRIPT_END"
    assert marker not in script_text
    argv = ["sbatch", "--parsable", f"--job-name={HOLD_JOB_NAME}", *options,
            f"--time={render_slurm_time(limit_s)}", f"--output={paths.log}", paths.script]
    return (f"cat > {_remote_quote(paths.script)} <<'{marker}'\n{script_text}{marker}\n"
            f"_aw_out=$({_remote_join(argv)}) || exit $?\n"
            f'_aw_job=${{_aw_out%%;*}}\n'
            f'printf %s "$_aw_job" > {_remote_quote(paths.job)}\n'
            f'echo "$_aw_job"\n')


def state_script(paths: HoldPaths) -> str:
    """Shell that prints everything the app knows about the hold, section by section (:func:`parse_state`)."""
    q = {name: _remote_quote(getattr(paths, name)) for name in ("job", "granted", "status", "ended")}
    return (f'echo "##job"; cat {q["job"]} 2>/dev/null; echo\n'
            f'_aw_job=$(cat {q["job"]} 2>/dev/null)\n'
            f'echo "##scontrol"; [ -n "$_aw_job" ] && scontrol show job -o "$_aw_job" 2>/dev/null; echo\n'
            f'echo "##granted"; cat {q["granted"]} 2>/dev/null\n'
            f'echo "##status"; cat {q["status"]} 2>/dev/null\n'
            f'echo "##ended"; cat {q["ended"]} 2>/dev/null\n'
            # The node's own clock: `granted_at` was written by the node, so the time left is read on the node's
            # clock too and no two machines' clocks are compared.
            'echo "##now"; date +%s\n'
            "exit 0\n")      # a missing file is an answer, not a failure: only ssh itself failing may exit non-zero


def cancel_script(paths: HoldPaths) -> str:
    """Shell that asks the hold to end (the ``release`` marker, so the script reports a release rather than a
    cancel) and cancels its job, but only if the job is this user's (:func:`services.remote_jobs._only_my_job`)."""
    return (f'[ -d {_remote_quote(paths.dir)} ] && touch {_remote_quote(paths.release)}\n'
            f'_aw_jid=$(cat {_remote_quote(paths.job)} 2>/dev/null)\n'
            '[ -z "$_aw_jid" ] && exit 0\n'
            + _only_my_job("_aw_jid", "AW_NOTMINE") +
            'command -v scancel >/dev/null 2>&1 && scancel -u "$_aw_me" "$_aw_jid" 2>/dev/null\n'
            'echo "AW_SCANCELLED $_aw_jid"\n')


@dataclass(frozen=True)
class HoldState:
    """What the host says about the hold now. ``phase`` is one of ``none`` (no hold), ``queued`` (the scheduler has
    not given a node), ``starting`` (a node is given and still coming up, or the script has not started),
    ``granted``, ``busy`` (granted and a run is using it) or ``ended``."""

    phase: str = "none"
    job_id: str = ""
    node: str = ""
    gpus: str = ""
    cpus: str = ""
    granted_at: int = 0
    limit_s: int = 0
    idle_s: int = 0
    ended_reason: str = ""
    slurm_state: str = ""
    slurm_reason: str = ""
    #: The job's GRES as ``--gres`` takes it (``gpu:1``), from ``scontrol``; a step asks for it explicitly, because
    #: whether a step inherits the job's GRES varies with the Slurm version.
    gres: str = ""
    #: The node's clock when the state was read (epoch seconds), for :attr:`time_left_s`.
    now_s: int = 0

    @property
    def live(self) -> bool:
        return self.phase in ("queued", "starting", "granted", "busy")

    @property
    def time_left_s(self) -> int | None:
        """Seconds until Slurm ends the node at its time limit, ``None`` when that is not known."""
        if not (self.granted_at and self.limit_s and self.now_s):
            return None
        return max(0, self.limit_s - (self.now_s - self.granted_at))


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("##") and line[2:] in ("job", "scontrol", "granted", "status", "ended", "now"):
            current = line[2:]
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def _pairs(text: str, sep: str = "\n") -> dict[str, str]:
    pairs = {}
    for part in text.split(sep):
        key, eq, value = part.partition("=")
        if eq:
            pairs[key.strip()] = value.strip()
    return pairs


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _job_gres(scontrol: dict[str, str]) -> str:
    """``gpu:1`` from a job's ``TresPerNode=gres/gpu:1`` (Slurm 21.08+) or ``Gres=gpu:1`` (older); ``""`` for none."""
    raw = scontrol.get("TresPerNode") or scontrol.get("Gres") or ""
    if raw in ("", "(null)", "N/A"):
        return ""
    return ",".join(item for item in (re.sub(r"^gres[/:]", "", part) for part in raw.split(",")) if item)


def parse_state(text: str) -> HoldState:
    """:class:`HoldState` from :func:`state_script`'s output."""
    s = _sections(text)
    job = s.get("job", "")
    if not job:
        return HoldState()
    sc = dict(re.findall(r"(\w+)=(\S*)", s.get("scontrol", "")))
    granted = _pairs(s.get("granted", ""))
    status = _pairs(s.get("status", ""))
    slurm_state = (sc.get("JobState") or "").upper()
    common = dict(job_id=job, node=granted.get("node") or sc.get("NodeList", "").replace("(null)", ""),
                  gpus=granted.get("gpus", ""), cpus=granted.get("cpus", ""),
                  granted_at=_int(granted.get("granted_at", "")), limit_s=_int(granted.get("limit_s", "")),
                  idle_s=_int(status.get("idle_s", "")), slurm_state=slurm_state,
                  slurm_reason=sc.get("Reason", ""), gres=_job_gres(sc), now_s=_int(s.get("now", "")))
    ended = s.get("ended", "")
    if ended:
        return HoldState(phase="ended", ended_reason=ended, **common)
    if slurm_state in _TERMINAL_STATES:
        return HoldState(phase="ended", ended_reason=f"the scheduler ended the job ({slurm_state})", **common)
    if not slurm_state:
        return HoldState(phase="ended", ended_reason="the scheduler no longer lists the job", **common)
    if slurm_state == "PENDING":
        return HoldState(phase="queued", **common)
    if slurm_state == "RUNNING" and granted:
        return HoldState(phase="busy" if status.get("state") == "busy" else "granted", **common)
    return HoldState(phase="starting", **common)      # CONFIGURING, or RUNNING with the script not yet started


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h} h {m} min" if h else f"{m} min"


class GrantedNode:
    """One host's granted node: asking for it, reading it, giving it back.

    *cache* is the host's ``DatasetCache`` (its ``_ssh_script`` makes the short control calls, and the heartbeat
    beats through it); *log_fn* gets the lines the run log shows; *on_state* is told each state the request sees,
    from the thread that is polling, so a GUI caller must hand it on through a queued signal. *sleep* and *clock*
    are for tests.

    :meth:`request` blocks until the node is granted, the request fails, or :meth:`release` is called from another
    thread; run it on a worker.
    """

    def __init__(self, host: RemoteHost, cache: DatasetCache, *, log_fn: Callable[[str], None],
                 on_state: Callable[[HoldState], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.host = host
        self.paths = hold_paths(host)
        self._cache = cache
        self._log = log_fn
        #: Told each state the request sees, from the thread that is polling; assign to listen after construction.
        self.on_state: Callable[[HoldState], None] = on_state or (lambda _s: None)
        self._sleep = sleep
        self._clock = clock
        self._heartbeat: _UploadHeartbeat | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        #: Whether this handle asked for the node (:meth:`request`) rather than found one already granted
        #: (:meth:`attach`). Quitting releases the first; it only stops beating for the second, which another window
        #: may still be using — the node's own script ends it two minutes after the last beat.
        self.owned = False

    def listen(self, *, log_fn: Callable[[str], None] | None = None,
               on_state: Callable[[HoldState], None] | None = None) -> None:
        """Point this handle's log lines and state updates at a new listener. The registry hands the same handle to every
        caller in the process, so a dialog opened a second time would otherwise keep talking to the first one's."""
        if log_fn is not None:
            self._log = log_fn
        if on_state is not None:
            self.on_state = on_state

    # ── talking to the host ──────────────────────────────────────────────────────────────────────────────────────
    def _script(self, script: str, *, check: bool = False):
        return self._cache._ssh_script(script, check=check, timeout_s=CONTROL_TIMEOUT_S)

    def state(self) -> HoldState:
        """The hold as the host reports it now. A host that cannot be reached is a ``HoldError``: guessing "no
        hold" would let a second request run beside a node that is still there."""
        result = self._script(state_script(self.paths))
        if result.returncode != 0:
            said = (result.stderr or "").strip().splitlines()
            raise HoldError(f"could not read the granted node's state (ssh exit {result.returncode}): "
                            f"{said[-1] if said else 'no output'}")
        return parse_state(result.stdout or "")

    def attach(self) -> HoldState:
        """The hold's state, and a heartbeat for it if it is live and this process is not yet beating for it.

        A node granted from another window, or by an earlier run of this app that crashed inside the two-minute
        window, is found here and kept: without a heartbeat the node's own script would release it two minutes
        later, out from under a user who is about to use it."""
        state = self.state()
        if state.live:
            with self._lock:
                if self._heartbeat is None:
                    self._heartbeat = _UploadHeartbeat(self._cache, self.paths.heartbeat, log_fn=self._log)
                    self._heartbeat.beat()
                    self._heartbeat.start()
        return state

    def _partition_cap(self, partition: str) -> int | None:
        result = self._script(partition_limit_script(partition))
        return parse_partition_limit(result.stdout or "") if result.returncode == 0 else None

    # ── request ──────────────────────────────────────────────────────────────────────────────────────────────────
    def request(self) -> HoldState:
        """Ask the scheduler for the node and wait until it is granted. Returns the granted state.

        Raises :class:`HoldQueueWaitSpent` when the host's Max queue wait runs out (the request is cancelled),
        and :class:`HoldError` for anything else that stops it, including :meth:`release` during the wait.
        """
        existing = self.state()
        if existing.live:
            raise HoldError(f"a node is already {existing.phase} for this host (job {existing.job_id}); release it "
                            "before requesting another.")
        self._cancelled.clear()
        self.owned = True
        launcher = self.host.launch_command
        options, user_excluded, partition = sbatch_options(launcher)
        cap = self._partition_cap(partition)
        limit_s = hold_time_limit_s(self.host, cap)
        if cap and limit_s < int(self.host.granted_time_limit_h) * 3600:
            self._log(f"[remote] granted node: time limit {self.host.granted_time_limit_h} h, lowered to "
                      f"{_duration(limit_s)} by the partition's limit.\n")
        self._script(prepare_script(self.paths), check=True)
        self._heartbeat = _UploadHeartbeat(self._cache, self.paths.heartbeat, log_fn=self._log)
        self._heartbeat.beat()           # before the job can look, so the first thing it sees is a heartbeat
        self._heartbeat.start()
        try:
            return self._ask(options, user_excluded, limit_s)
        except BaseException:
            self.release("the request did not complete")
            raise

    def _ask(self, options: list[str], user_excluded: list[str], limit_s: int) -> HoldState:
        script_text = compose_hold_script(
            self.paths, limit_s=limit_s, idle_release_s=int(self.host.granted_idle_release_min) * 60)
        queue_wait = queue_wait_seconds(self.host)
        began = self._clock()
        failed_nodes: list[str] = []
        while True:
            excluded = [*user_excluded, *failed_nodes]
            extra = [f"--exclude={','.join(excluded)}"] if excluded else []
            submitted = self._script(submit_script(self.paths, script_text, [*options, *extra], limit_s=limit_s))
            if submitted.returncode != 0:
                raise HoldError("the scheduler refused the request: "
                                f"{(submitted.stderr or submitted.stdout or '').strip() or 'no reason given'}")
            self._log(f"[remote] granted node: asked the scheduler for a node (job {submitted.stdout.strip()}, "
                      f"time limit {_duration(limit_s)}).\n")
            boot_failed = self._wait(began, queue_wait)
            if boot_failed is None:
                return self.state()
            failed_nodes.append(boot_failed)
            self._log(f"[remote] granted node: node {boot_failed} failed to boot; asking for another.\n")
            self._nap(BOOT_RETRY_PAUSE_S)

    def _nap(self, seconds: float) -> None:
        if self._cancelled.is_set():
            raise HoldError("the request was cancelled.")
        self._sleep(seconds)

    def _wait(self, began: float, queue_wait: int) -> str | None:
        """Poll until the hold is granted (``None``), its node failed to boot (that node's name), or it cannot be."""
        while True:
            if self._cancelled.is_set():
                raise HoldError("the request was cancelled.")
            state = self.state()
            self.on_state(state)
            if state.phase in ("granted", "busy"):
                self._log(f"[remote] granted node: {state.node} (job {state.job_id}) is granted"
                          f"{' — ' + state.gpus if state.gpus else ''}.\n")
                return None
            if state.slurm_state in _BOOT_FAILED_STATES and not state.granted_at:
                return state.node or "?"
            if state.phase == "ended":
                raise HoldError(f"the node was not granted: {state.ended_reason}.")
            if queue_wait and self._clock() - began > queue_wait:
                self.release("it waited out this host's Max queue wait")
                raise HoldQueueWaitSpent(
                    f"no node was granted within {queue_wait // 60} min, this host's Max queue wait — "
                    f"cancelled job {state.job_id}.")
            self._nap(HOLD_POLL_S)

    # ── release ──────────────────────────────────────────────────────────────────────────────────────────────────
    def stop_beating(self) -> None:
        """Stop this process's heartbeat for the node and leave the node alone."""
        with self._lock:
            heartbeat, self._heartbeat = self._heartbeat, None
        if heartbeat is not None:
            heartbeat.stop()

    def release(self, reason: str) -> None:
        """Give the node back: stop beating, ask the script to end, cancel the job. Idempotent. A request being
        waited on in another thread is told to stop."""
        self._cancelled.set()
        self.stop_beating()
        try:
            result = self._script(cancel_script(self.paths))
        except Exception as exc:
            self._log(f"[remote] granted node: WARN could not reach the host to release it ({exc}); it releases "
                      "itself within two minutes of the app's last heartbeat.\n")
            return
        out = result.stdout or ""
        if "AW_NOTMINE" in out:
            self._log("[remote] granted node: the hold's job belongs to another user — left running.\n")
        elif "AW_SCANCELLED" in out:
            self._log(f"[remote] granted node: released — {reason}.\n")
        elif result.returncode != 0:
            self._log(f"[remote] granted node: WARN could not release it (ssh exit {result.returncode}); it "
                      "releases itself within two minutes of the app's last heartbeat.\n")


# ── running on the granted node ──────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StepTarget:
    """Where a run goes while a node is granted: the hold job, and what its steps ask for."""

    job_id: str
    node: str = ""
    cpus: int = 0
    gres: str = ""

    @classmethod
    def from_state(cls, state: HoldState) -> StepTarget:
        return cls(job_id=state.job_id, node=state.node, cpus=_int(state.cpus), gres=state.gres)


def step_launcher(target: StepTarget) -> str:
    """The scheduler command a run uses on a granted node, in place of the host's ``srun …`` line: a step of the hold
    job, one task on its node.

    ``--overlap`` because the hold's batch script holds the job's CPUs while it waits (it only sleeps), and the
    step shares them. The job's CPU count and GRES are asked for explicitly: what a step inherits has varied with
    the Slurm version (``--exact`` and ``--whole`` changed the defaults), and asking for exactly what the job has is
    valid on all of them. A host's own ``--time`` is not here: the job's time limit governs a step.
    """
    parts = ["srun", f"--jobid={target.job_id}", "--overlap", "--nodes=1", "--ntasks=1"]
    if target.cpus:
        parts.append(f"--cpus-per-task={target.cpus}")
    if target.gres:
        parts.append(f"--gres={target.gres}")
    return " ".join(parts)


# ── the handle on a host's granted node, shared by Manage Remotes and every run ─────────────────────────────────────

_NODES: dict[str, GrantedNode] = {}
_NODES_LOCK = threading.Lock()


def node_for(host: RemoteHost, cache: DatasetCache, *, log_fn: Callable[[str], None],
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> GrantedNode:
    """The one :class:`GrantedNode` for *host* in this process, made on first use. A handle made earlier keeps its own
    cache, log and clock; the arguments are used only when there is none yet."""
    with _NODES_LOCK:
        node = _NODES.get(host.name)
        if node is None or node.host.ssh_alias != host.ssh_alias:
            node = _NODES[host.name] = GrantedNode(host, cache, log_fn=log_fn, sleep=sleep, clock=clock)
        return node


def live_nodes() -> list[GrantedNode]:
    """Every handle this process has made; the app releases them when it quits."""
    with _NODES_LOCK:
        return list(_NODES.values())


# ── a run's claim on the node ───────────────────────────────────────────────────────────────────────────────────────

def claim_script(paths: HoldPaths, *, stale_s: int = MID_BATCH_HEARTBEAT_STALE_S) -> str:
    """Shell that takes the node for one run: creates the ``busy`` marker, atomically (``set -C`` makes the create fail
    if it exists, so two windows cannot both win). A marker older than *stale_s* is a run whose app died and is
    removed first — the hold script reads it as not busy by the same rule. Prints ``AW_LEASED`` or ``AW_BUSY``."""
    b = _remote_quote(paths.busy)
    return (f'[ -d {_remote_quote(paths.dir)} ] || {{ echo AW_NOHOLD; exit 0; }}\n'
            f'if [ -e {b} ] && [ -n "$(find {b} -mmin +{max(1, stale_s // 60)} 2>/dev/null)" ]; then rm -f {b}; fi\n'
            f'if ( set -C; : > {b} ) 2>/dev/null; then echo AW_LEASED; else echo AW_BUSY; fi\n'
            "exit 0\n")


def run_estimate_s(host: RemoteHost, n_wells: int, batch_size: int, nanowell: bool) -> int:
    """How long the run will want the node, from the scheduler command's own ``--time`` (one batch's budget, as
    :func:`~services.remote_config.scale_launcher_time` reads it) times the batches, plus the one batch's allowance
    for transfers it adds. ``0`` when the command sets no ``--time``: nothing to compare."""
    from services.remote_config import launcher_time_seconds
    from services.remote_reservation import batches_upper_bound

    per = launcher_time_seconds(host.compute_launcher)
    batches = batches_upper_bound(n_wells, batch_size, nanowell=nanowell)
    return per * (batches + 1) if per and batches else per


class NodeLease:
    """One run's claim on the granted node: the ``busy`` marker, kept fresh by a heartbeat for as long as the run lives.

    The marker is what stops the node's idle clock and its two-minute app-gone rule while this run uses the node, and
    what makes a second run wait. It is taken before the run stages anything, because preparing and uploading a large
    plate can take longer than the idle limit."""

    def __init__(self, node: GrantedNode, state: HoldState, *, log_fn: Callable[[str], None]) -> None:
        self.node = node
        self.state = state
        self._log = log_fn
        self._beater: _UploadHeartbeat | None = _UploadHeartbeat(node._cache, node.paths.busy, log_fn=log_fn)
        self._beater.beat()
        self._beater.start()

    @property
    def target(self) -> StepTarget:
        return StepTarget.from_state(self.state)

    def fits(self, needed_s: int) -> bool:
        """Whether a run wanting *needed_s* seconds finishes before Slurm ends the node. Unknown counts as fitting."""
        left = self.state.time_left_s
        return needed_s <= 0 or left is None or needed_s <= left

    def release(self) -> None:
        """Give the node back to the idle clock. Idempotent. A failure to remove the marker is said, because the
        node then reads as busy until the marker goes stale."""
        beater, self._beater = self._beater, None
        if beater is None:
            return
        beater.stop()
        try:
            result = self.node._script(f"rm -f {_remote_quote(self.node.paths.busy)}\n")
            failed = result.returncode != 0
        except Exception as exc:
            failed, result = True, exc
        if failed:
            self._log("[remote] WARN granted node: could not tell the node this run is over; it counts the node as "
                      f"busy for up to {MID_BATCH_HEARTBEAT_STALE_S // 60} min ({getattr(result, 'stderr', result)}).\n")


def claim_node(host: RemoteHost, cache: DatasetCache, *, log_fn: Callable[[str], None],
               stop_event: threading.Event | None = None, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> NodeLease | None:
    """Take the host's granted node for a run, or ``None`` when the run should ask for a node of its own (no scheduler,
    no granted node, one that has been released, or a host that cannot be reached — the run's own ssh then says why).

    A node that is still being granted, or is in use by another run, is waited for: never a second node beside it.
    The wait is bounded by the host's Max queue wait (``0`` = no bound) and ends at once on Stop."""
    if not host.compute_launcher.strip():
        return None
    node = node_for(host, cache, log_fn=log_fn, sleep=sleep, clock=clock)
    try:
        state = node.attach()
    except HoldError:
        # Not the error itself: the run's own ssh will report the host, and quoting its text here made the run's
        # headline read a connection failure it had not had. What matters is that a granted node, if there is one,
        # is not being used.
        log_fn("[remote] WARN granted node: could not tell whether this host has a granted node, so this run asks "
               "for a node of its own.\n")
        return None
    wait_s, began, said = queue_wait_seconds(host), clock(), False
    while True:
        if state.phase in ("none", "ended"):
            if state.phase == "ended":
                log_fn(f"[remote] granted node {state.node or '?'} (job {state.job_id}) was released: "
                       f"{state.ended_reason}; this run asks for a node of its own.\n")
            return None
        if state.phase in ("granted", "busy"):
            answer = (node._script(claim_script(node.paths)).stdout or "").strip()
            if answer == "AW_LEASED":
                log_fn(f"[remote] granted node: using {state.node}{' (' + state.gpus + ')' if state.gpus else ''}, "
                       f"job {state.job_id}.\n")
                return NodeLease(node, state, log_fn=log_fn)
            why = "busy with another run" if answer == "AW_BUSY" else "not claimable"
        else:
            why = "still being granted"
        if not said:
            log_fn(f"[remote] scheduler: the granted node is {why}; waiting for it.\n")
            said = True
        if stop_event is not None and stop_event.is_set():
            raise HoldError("the run was stopped while it waited for the granted node.")
        if wait_s and clock() - began > wait_s:
            raise HoldQueueWaitSpent(f"the granted node stayed {why} for {wait_s // 60} min, this host's Max queue "
                                     "wait; no node of its own is asked for beside it.")
        sleep(HOLD_POLL_S)
        state = node.state()


class GrantedRun:
    """What one remote run does about a granted node: claim it as the run starts, check the run fits in the time
    left once the plate is staged, and give the claim back however the run ends. ``PipelineRunner`` holds one per run.

    While a node is claimed, :attr:`active` is true, the run's ``RemoteRunner.step_target`` names it, and the early
    request for a node of the run's own is not made."""

    def __init__(self, runner, *, batch_size: int, nanowell: bool, log_fn: Callable[[str], None],
                 stop_event: threading.Event, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._runner = runner
        self._batch_size = batch_size
        self._nanowell = nanowell
        self._log = log_fn
        self._stop = stop_event
        self._sleep = sleep
        self._clock = clock
        self.lease: NodeLease | None = None

    @property
    def active(self) -> bool:
        return self.lease is not None

    def claim(self) -> None:
        self.lease = self._runner.claim_granted_node(log_fn=self._log, stop_event=self._stop, sleep=self._sleep,
                                                     clock=self._clock)

    def check_fit(self, n_wells: int) -> None:
        """Once the well count is known: a run that would outlast the node's time limit gets a fresh node, with a fresh
        limit, rather than being killed by Slurm part-way."""
        lease = self.lease
        needed = run_estimate_s(self._runner.host, n_wells, self._batch_size, self._nanowell)
        if lease is None or lease.fits(needed):
            return
        left = lease.state.time_left_s or 0
        self._log(f"[remote] scheduler: this run needs about {_duration(needed)} and the granted node has "
                  f"{_duration(left)} left; releasing it and requesting a fresh one.\n")
        node = lease.node
        lease.release()
        node.release("the run would not fit in its time left")
        self._wait_until_ended(node)
        node.request()
        self.claim()

    def _wait_until_ended(self, node: GrantedNode, timeout_s: float = 120.0) -> None:
        began = self._clock()
        while node.state().live:
            if self._clock() - began > timeout_s:
                raise HoldError("the granted node was asked to release and is still listed after "
                                f"{int(timeout_s)} s; a fresh one was not requested.")
            self._sleep(2)

    def release(self) -> None:
        lease, self.lease = self.lease, None
        self._runner.step_target = None
        if lease is not None:
            lease.release()


def release_all(reason: str, *, timeout_s: float = 10.0) -> bool:
    """What quitting does about granted nodes: release the ones this process asked for, all at once, and stop beating
    for the ones it only found (another window may be using those; the node's script ends them two minutes after the
    last beat). Waits at most *timeout_s* in all; whether everything finished in time. A release that does not finish
    is not a leak: the node ends itself two minutes after the app's heartbeat stops."""
    def one(node: GrantedNode) -> None:
        if node.owned:
            node.release(reason)
        else:
            node.stop_beating()

    threads = [threading.Thread(target=one, args=(n,), daemon=True, name="aw-release-granted-node")
               for n in live_nodes()]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout_s
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return not any(t.is_alive() for t in threads)


# ── what Manage Remotes shows ───────────────────────────────────────────────────────────────────────────────────────

def can_request(host: RemoteHost) -> tuple[bool, str]:
    """``(whether Request a node applies to *host*, why not)``: only a Slurm ``srun`` scheduler command can be turned into
    the hold job (:func:`sbatch_options`)."""
    if not host.compute_launcher.strip():
        return False, "Requesting a node needs the host to run compute on a node it schedules."
    if not is_srun(host.compute_launcher):
        return False, "Requesting a node needs a Slurm (srun) scheduler command."
    return True, ""


def _gpu_words(gpus: str) -> str:
    """``1× NVIDIA A10G`` from ``NVIDIA A10G``; ``2× NVIDIA A10G`` from the same name twice."""
    names = [g.strip() for g in gpus.split(",") if g.strip()]
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{n}× {name}" for name, n in counts.items())


def describe_state(state: HoldState, idle_release_min: int) -> list[str]:
    """The lines Manage Remotes shows under the host list for *state*: what the node is, how long it has been granted
    and has left, and what will release it. Empty lines are never returned."""
    if state.phase == "none":
        return ["No node granted."]
    if state.phase == "ended":
        return [f"Released — {state.ended_reason}." if state.ended_reason else "Released."]
    if state.phase == "queued":
        reason = f" ({state.slurm_reason})" if state.slurm_reason and state.slurm_reason != "None" else ""
        return [f"Queued — waiting for the scheduler{reason}."]
    if state.phase == "starting":
        return [f"Node {state.node} is starting…" if state.node else "Node starting…"]
    first = " · ".join(p for p in (f"Node {state.node}", f"job {state.job_id}", _gpu_words(state.gpus)) if p)
    lines = [first]
    if state.granted_at and state.now_s:
        left = state.time_left_s
        lines.append(f"Granted {_duration(state.now_s - state.granted_at)} ago"
                     + (f" · {_duration(left)} left" if left is not None else ""))
    if state.phase == "busy":
        lines.append("In use by a run.")
    elif idle_release_min:
        lines.append(f"Idle {_duration(state.idle_s)} — released after {idle_release_min} min idle.")
    else:
        lines.append(f"Idle {_duration(state.idle_s)} — idle release is off.")
    return lines
