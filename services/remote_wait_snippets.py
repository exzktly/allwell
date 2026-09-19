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

from services.remote_transport import _remote_quote

# How long a granted allocation will wait for that file before giving up. The
# node is idle and billed for every second of it, so this is a backstop against
# a dead uploader holding a node, not a budget to be spent — a healthy upload
# writes the sentinel as soon as rsync returns. Exit code is distinct so the
# failure reads as "the upload never finished", not as a pipeline crash.
UPLOAD_WAIT_TIMEOUT_S = 7200
UPLOAD_WAIT_POLL_S = 3
_UPLOAD_WAIT_EXIT = 75

# Touched on the remote every ``UPLOAD_HEARTBEAT_S`` for as long as an upload is
# actually in flight. The deadline above is a backstop against a hung uploader;
# this is the answer to a *dead* one — the app being killed, the laptop closing,
# the network going — where waiting out two hours of idle, billed node is the
# wrong behaviour. A heartbeat that stops advancing frees the node in about a
# minute regardless of how long the upload legitimately needed.
#
# The file is touched over ssh, so its mtime is the *remote* clock and the job
# comparing it against its own ``date`` is not measuring clock skew between two
# machines.
UPLOAD_HEARTBEAT_S = 30
UPLOAD_HEARTBEAT_STALE_S = 120


def _heartbeat_stale_snippet(heartbeat: str, message: str) -> tuple[str, str]:
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
    """
    if not heartbeat:
        return "", ""
    hb_q = _remote_quote(heartbeat)
    misses = max(1, int(UPLOAD_HEARTBEAT_STALE_S // UPLOAD_WAIT_POLL_S))
    init = "_aw_miss=0; _aw_warn=0; "
    check = (
        f'if [ -e {hb_q} ]; then '
        f'_aw_miss=0; '
        f'_aw_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
        f'stat -f %m {hb_q} 2>/dev/null || echo ""); '
        f'_aw_now=$(date +%s); '
        f'if [ -z "$_aw_hb" ]; then '
        f'if [ "$_aw_warn" -eq 0 ]; then _aw_warn=1; '
        f'echo "[remote] WARN this host cannot report a file\'s modification '
        f'time, so the job cannot tell whether the app is still there — it '
        f'will hold the node until the pipeline runs or the walltime ends"; '
        f'fi; '
        f'elif [ "$((_aw_now - $_aw_hb))" -gt {UPLOAD_HEARTBEAT_STALE_S} ]; '
        f'then echo "{message}"; exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'else '
        f'_aw_miss=$((_aw_miss+1)); '
        f'if [ "$_aw_miss" -gt {misses} ]; then '
        f'echo "{message}"; exit {_UPLOAD_WAIT_EXIT}; fi; '
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
    waited = int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S)
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
        f'_aw_w=0; {init}'
        f'while [ ! -f {sentinel_q} ]; do '
        f'_aw_w=$((_aw_w+1)); '
        f'{stale}'
        f'if [ "$_aw_w" -gt {waited} ]; then '
        f'echo "[remote] ERROR the upload never completed — giving the node '
        f'back after {UPLOAD_WAIT_TIMEOUT_S}s"; exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        f'echo "[remote] scheduler: input is complete — starting the pipeline"; '
    )
