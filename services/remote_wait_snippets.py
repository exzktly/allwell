"""The shell a granted node runs while it waits, and the clocks behind it.

Two remote waiting loops ask the same question — the single-shot job waiting
for its input upload to finish, and the batch driver waiting for its next
batch — and both are running **inside a granted allocation**, on a node that is
idle and billed for every second of it. So the question they ask is "has the
app that is supposed to be feeding me stopped?", and the cost of getting it
wrong in either direction is real: answer yes too eagerly and a healthy run
throws away a node it waited in a queue for; answer no forever and a dead
uploader parks a node for two hours.

This module holds that shell and the constants that time it, out of
``services/remote_runner.py`` because it is one self-contained job with one
dependency (``_remote_quote``) and because the file it came from is the largest
in the repository and capped. Every name is re-exported there, so no call site
and no test moved with it.

Nothing here runs anything. These functions return **strings of shell** that
are composed into the command sent over ssh, which is why they can be tested
by reading what they produce.
"""

from __future__ import annotations

from services.remote_srun_retry import HANDOVER_TAKEN_LINE, HANDOVER_WAIT_LINE
from services.remote_transport import _remote_quote

# How long a granted allocation will wait for that file before giving up. The
# node is idle and billed for every second of it, so this is a backstop against
# a dead uploader holding a node, not a budget to be spent — a healthy upload
# writes the sentinel as soon as rsync returns. Exit code is distinct so the
# failure reads as "the upload never finished", not as a pipeline crash.
UPLOAD_WAIT_TIMEOUT_S = 7200
UPLOAD_WAIT_POLL_S = 3
_UPLOAD_WAIT_EXIT = 75

# The deadline counts only the polls at which a fresh heartbeat did *not* vouch for
# the app. A healthy upload of a large batch can take longer than any fixed wait
# — six nanowell wells are ~48 GB, which needs > 53 Mbit/s to land in two hours —
# and while the app is beating, nothing about the wait is wrong. What the deadline
# still bounds is the wait nothing can vouch for: no heartbeat configured, or a
# host that cannot report a file's modification time.

# Touched on the remote every ``UPLOAD_HEARTBEAT_S`` for as long as an upload is
# actually in flight. The deadline above is a backstop against a hung uploader;
# this is the answer to a *dead* one — the app being killed, the laptop closing,
# the network going — where waiting out two hours of idle, billed node is the
# wrong behaviour. A heartbeat that stops advancing frees the node in about a
# minute regardless of how long the upload legitimately needed.
#
# The file is touched over ssh from the login node, and the job reading it runs on
# a compute node. On a shared filesystem the mtime is the *file server's* clock,
# and the compute node's ``date`` is a third machine's: more than
# ``UPLOAD_HEARTBEAT_STALE_S`` of skew between them read every beat as stale and
# gave the node back at every batch boundary. So "now" is read the same way — the
# mtime of a probe file the job touches beside the heartbeat, which the same
# server stamps — and ``date`` is only the fallback for a directory the job cannot
# write to.
UPLOAD_HEARTBEAT_S = 30
UPLOAD_HEARTBEAT_STALE_S = 120

# The same question asked *while a batch is running*, by a watchdog beside the
# pipeline. Silence there is answered more patiently, because the answer is
# costlier to get wrong: a false "the app is gone" suspends a batch that was
# doing useful work and leaves the run waiting for a Resume. Ten minutes of no
# beat is not a slow network. The app crashing or being force-quit leaves its
# ssh child alive as an orphan, so the allocation would otherwise be held until
# the batch ended — hours, on a large batch — with nobody left to fetch it.
MID_BATCH_HEARTBEAT_STALE_S = 600
MID_BATCH_WATCH_POLL_S = 30

# While a batch runs, the driver says so this often — ``BATCH_STILL_RUNNING`` —
# from the same background subshell as the watchdog. A batch is the one stretch
# of a run with nothing else on the stream for hours, and silence there looked
# exactly like a stream that had died: the app treats any line as proof of life.
BATCH_TICK_S = 300
BATCH_STILL_RUNNING = "[remote] batch-driver: batch {i} still running ({m}m)"

# A batch over its time budget is sent SIGTERM, which the pipeline answers as a
# cooperative suspend — finishing the FOV-groups in flight — and a hung well
# never reaches that boundary. So the budget is ``timeout -k``: this long after
# the SIGTERM, SIGKILL. And because ``timeout`` returns as soon as its own child
# exits, a ``conda run`` wrapper that dies on the SIGTERM would leave the real
# pipeline running as an orphan, writing ``out/`` into the next batch; the driver
# therefore also waits this long for the batch's whole process group to go and
# kills what is left.
BATCH_BUDGET_GRACE_S = 300
BATCH_OVER_BUDGET = "[remote] ERROR batch {i} exceeded its time budget"

# How long a batch's process group is given to empty by itself, once its
# pipeline has returned, before anything left in it counts as having outlived
# the batch. A pipeline that ran a ``spawn`` process pool leaves its
# ``multiprocessing`` resource tracker behind it for a few milliseconds, by
# design — it exits when its parent's pipe closes — and the driver's first
# ``kill -0`` used to land inside that window on every batch: a WARN that
# "processes of its pipeline outlived it" and a second's wait, on a clean run.
# A warning that fires on every batch is one nobody reads when it is true.
BATCH_EXIT_SETTLE_S = 2


def _heartbeat_stale_snippet(heartbeat: str, message: str, *,
                             stale_s: int = UPLOAD_HEARTBEAT_STALE_S,
                             poll_s: int = UPLOAD_WAIT_POLL_S,
                             on_stale: str = "") -> tuple[str, str]:
    """``(init, check)`` shell for "has the app stopped beating?".

    Both waiting loops — the single-shot job waiting for its upload and the
    batch driver waiting for its next batch — ask the same question, so they
    ask it with the same shell rather than each carrying a copy that can drift.

    Two things it deliberately does not do, because the cost of a wrong answer
    is a granted node thrown away:

    * **``stat`` is not assumed to be GNU.** ``stat -c %Y`` is coreutils;
      ``stat -f %m`` is the BSD spelling. A login node with only the latter used
      to yield an empty mtime, which read as "epoch 0" and therefore as
      infinitely stale — every run on such a host aborted the instant it was
      granted a node, blaming the app. When neither spelling works the timestamp
      is unknowable, so the check says so once and stops judging: losing the
      early hand-back is a far smaller harm than never running at all. The ssh
      dying still tears the job down.
    * **A missing file is not instantly fatal.** The app touches the heartbeat
      before the job can look, but the two see it across a shared filesystem
      whose metadata may lag. An absent file is tolerated for the same window as
      a stale one before the node is handed back.

    * **The age it reads is never more than the true age.** The app's beats
      land at most ``UPLOAD_HEARTBEAT_S`` + ``remote_heartbeat.BEAT_TIMEOUT_S``
      (105 s) apart, against *stale_s* (120 s) — a margin that holds only for
      an age read correctly. Two things read it high on a shared filesystem.
      The probe that gives "now" was touched *after* the heartbeat was read, so
      on the same stalled filesystem that slows the beats its own delay was
      added to the age; it is touched first now, so a beat landing meanwhile is
      seen. And ``stat`` on an NFS client may answer from its attribute cache
      (``acregmax``, 60 s by default), which can still hold the mtime from
      before the last beat; the heartbeat is opened first, which under NFS's
      close-to-open consistency (the default, ``cto``) has the client fetch the
      file's attributes from the server.

    *stale_s* and *poll_s* time the check (the loop around it sleeps *poll_s*
    between calls), and *on_stale* is the shell run once the app is judged gone
    — by default the exit that hands the node back. Each check leaves
    ``$_aw_evidence`` is set just before the file is judged gone — how long ago it was last touched and by which
    clock (the file server's, read from a probe beside it, or this node's when the probe could not be written), or
    how many looks found no file — so *message* and *on_stale* can carry the measurement and not only the verdict.
    ``$_aw_fresh`` at ``1`` when a heartbeat it could read vouched for the app,
    which is what lets the waits' deadline stand aside for a healthy slow upload.
    """
    if not heartbeat:
        return "", ""
    hb_q = _remote_quote(heartbeat)
    misses = max(1, int(stale_s // poll_s))
    gone = f'echo "{message}"; {on_stale or f"exit {_UPLOAD_WAIT_EXIT}"}; '
    probe = hb_q + '".now.${BASHPID:-$$}"'
    mtime = 'stat -c %Y "$_aw_p" 2>/dev/null || stat -f %m "$_aw_p" 2>/dev/null'
    init = "_aw_miss=0; _aw_warn=0; _aw_fresh=0; "
    check = (
        f'_aw_fresh=0; '
        f'if [ -e {hb_q} ]; then '
        f'_aw_miss=0; '
        # "Now" by the clock that stamped the heartbeat: a probe beside it (see UPLOAD_HEARTBEAT_STALE_S). Taken
        # before the heartbeat is read, so the age can only be under-stated (see this function's docstring).
        f'_aw_p={probe}; _aw_now=""; '
        f'_aw_clock="this node\'s clock"; '
        f'if touch "$_aw_p" 2>/dev/null; then _aw_now=$({mtime} || echo ""); rm -f "$_aw_p" 2>/dev/null; fi; '
        f'if [ -n "$_aw_now" ]; then _aw_clock="the file server\'s clock"; else _aw_now=$(date +%s); fi; '
        f'true 2>/dev/null <{hb_q} || :; '
        f'_aw_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
        f'stat -f %m {hb_q} 2>/dev/null || echo ""); '
        f'if [ -z "$_aw_hb" ]; then '
        f'if [ "$_aw_warn" -eq 0 ]; then _aw_warn=1; '
        f'echo "[remote] WARN this host cannot report a file\'s modification '
        f'time, so the job cannot tell whether the app is still there — it '
        f'will hold the node until the pipeline runs or the walltime ends"; '
        f'fi; '
        f'elif [ "$((_aw_now - $_aw_hb))" -gt {int(stale_s)} ]; '
        f'then _aw_evidence="last touched $((_aw_now - _aw_hb)) s ago by $_aw_clock"; {gone}'
        f'else _aw_fresh=1; fi; '
        f'else '
        f'_aw_miss=$((_aw_miss+1)); '
        f'if [ "$_aw_miss" -gt {misses} ]; then '
        f'_aw_evidence="not found in $_aw_miss looks, {int(poll_s)} s apart"; {gone}fi; '
        f'fi; '
    )
    return init, check


def _wait_for_upload_snippet(sentinel: str, heartbeat: str = "") -> str:
    """Shell that blocks until *sentinel* exists, or gives the node back.

    Runs **inside** the allocation, so its output reaches the user on the same
    stdout as the rest of the run. Announcing both edges matters: a job that
    has been granted a node but is waiting on bytes looks exactly like a job
    that has hung, and the whole point of asking for the node early is that
    this window is expected.
    """
    sentinel_q = _remote_quote(sentinel)
    waited = max(1, int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S))
    # A stale heartbeat means the uploader is gone — killed, disconnected,
    # asleep — and nothing will ever write the sentinel. Checked before the
    # deadline because it is the case that actually happens; the deadline is
    # for an uploader that is alive but wedged.
    init, stale = _heartbeat_stale_snippet(
        heartbeat,
        f"[remote] ERROR the uploader stopped responding "
        f"({UPLOAD_HEARTBEAT_STALE_S}s without a heartbeat) — giving the "
        f"node back")
    return (
        f'if [ ! -f {sentinel_q} ]; then '
        f'echo "[remote] scheduler: node granted; waiting for the upload to '
        f'finish before starting the pipeline…"; fi; '
        f'_aw_w=0; _aw_fresh=0; {init}'
        f'while [ ! -f {sentinel_q} ]; do '
        f'{stale}'
        # Only the polls no fresh heartbeat vouched for count towards the deadline (see UPLOAD_WAIT_TIMEOUT_S).
        f'[ "$_aw_fresh" = 1 ] || _aw_w=$((_aw_w+1)); '
        f'if [ "$_aw_w" -gt {waited} ]; then '
        f'echo "[remote] ERROR the upload never completed — giving the node '
        f'back after {UPLOAD_WAIT_TIMEOUT_S}s with no heartbeat to say the upload was still going"; '
        f'exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        f'echo "[remote] scheduler: input is complete — starting the pipeline"; '
    )


def _wait_for_handover_snippet(command_file: str, heartbeat: str) -> str:
    """Shell that blocks until the app hands the job its command, or gives the node back.

    The job an early allocation request starts (``services.remote_reservation``)
    is queued before the app knows what the run will be: the plate is still
    being planned and staged on this machine, and the run directory, the
    pipeline's arguments and the batch count all come out of that. So the job
    waits for *command_file*, which the app writes once — atomically, by a
    rename — when the run is ready, and then runs it in this same shell.

    The same two clocks as :func:`_wait_for_upload_snippet` decide when to stop
    waiting: a heartbeat silent for ``UPLOAD_HEARTBEAT_STALE_S`` means the app
    is gone, and ``UPLOAD_WAIT_TIMEOUT_S`` of polls no fresh heartbeat vouched
    for is the backstop. Both edges are announced, because a job granted a node
    while the app is still planning looks exactly like a job that has hung.
    """
    command_q = _remote_quote(command_file)
    waited = max(1, int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S))
    init, stale = _heartbeat_stale_snippet(
        heartbeat,
        f"[remote] ERROR the app stopped responding while it prepared the run "
        f"({UPLOAD_HEARTBEAT_STALE_S}s without a heartbeat) — giving the node back")
    return (
        f'if [ ! -f {command_q} ]; then echo "{HANDOVER_WAIT_LINE}"; fi; '
        f'_aw_w=0; _aw_fresh=0; {init}'
        f'while [ ! -f {command_q} ]; do '
        f'{stale}'
        f'[ "$_aw_fresh" = 1 ] || _aw_w=$((_aw_w+1)); '
        f'if [ "$_aw_w" -gt {waited} ]; then '
        f'echo "[remote] ERROR the app never handed the run over — giving the node '
        f'back after {UPLOAD_WAIT_TIMEOUT_S}s with no heartbeat to say it was still preparing"; '
        f'exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        f'echo "{HANDOVER_TAKEN_LINE}"; '
    )
