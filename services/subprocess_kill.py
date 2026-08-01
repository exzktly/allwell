"""Cross-platform subprocess kill helper.

Sends SIGTERM to a subprocess's whole session/group, then escalates to
SIGKILL five seconds later if it's still alive. Honors the existing
contract used by ``PipelineRunner`` since v5.0.0 — extracted here so
the bootstrap-from-GUI dialog (v5.1.0) and any future runner share one
copy instead of forking the implementation.

POSIX: ``os.killpg`` against the captured pgid.
Windows: ``CTRL_BREAK_EVENT`` to the process group.

The pgid is captured **up front** before SIGTERM is sent — without that,
a 5-second escalation window could SIGKILL an unrelated process whose
PID got recycled. (Real bug we saw in v5.0.0 testing on a busy host.)
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

from util.aw_safe import swallowed


def signal_subprocess(proc: subprocess.Popen | None, *,
                      escalate: bool = True) -> None:
    """Terminate ``proc`` and its whole process group.

    Safe to call when ``proc`` is None or already exited — both no-op.
    Schedules a SIGKILL escalation 5 seconds later in case the
    subprocess ignores SIGTERM (rare in practice, but
    multiprocessing-spawned workers occasionally do this during
    shutdown).

    ``escalate=False`` sends the SIGTERM and stops there. That is for a
    *cooperative* stop, where the subprocess catches the signal and is expected
    to take a while winding down — a well finishing the FOV-group it is on can
    easily outlast five seconds, and the SIGKILL would destroy exactly the work
    the caller is trying to keep. The caller owns the fallback: a second,
    escalating call is still available if the process really is stuck.

    This is a **best-effort** kill and must never raise: it runs from the
    GUI's Stop handler, and an unhandled exception escaping a Qt slot
    aborts the whole application (PySide6 ≥ 6.5 calls ``qFatal``). Inputs
    can be a real ``Popen`` or a Popen-compatible remote wrapper whose
    ``poll()`` / ``pid`` may behave unexpectedly (network I/O, non-int
    pid), so every step is guarded.
    """
    try:
        if proc is None or proc.poll() is not None:
            return
    except Exception:
        # A wrapper whose poll() itself fails → nothing we can safely signal.
        return

    pgid: int | None = None
    if os.name == "posix":
        try:
            pid = proc.pid
            if isinstance(pid, int) and pid > 0:
                pgid = os.getpgid(pid)
        except Exception:
            pgid = None
        # CRITICAL: never signal our OWN process group. A subprocess that
        # wasn't started in a new session (e.g. a local rsync/ssh sub-step of an
        # incremental remote run) shares the GUI's process group, so os.killpg
        # against it would SIGTERM/SIGKILL the GUI itself — the "Stop quit the
        # whole app" bug. In that case fall back (pgid=None) to terminating just
        # the single process below.
        try:
            if pgid is not None and pgid == os.getpgrp():
                pgid = None
        except Exception:
            pgid = None

    try:
        if os.name == "posix":
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        else:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    except Exception:
        try:
            proc.terminate()
        except Exception:
            swallowed()  # best-effort: the process may already be gone

    def _force_kill_if_alive() -> None:
        # Re-check the live process handle, not just the cached pgid —
        # if the process exited cleanly within the 5 s window, the OS
        # may have re-used the PID for an unrelated process.
        try:
            if proc.poll() is not None:
                return
        except Exception:
            return
        try:
            if os.name == "posix" and pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            swallowed()

    if not escalate:
        return
    try:
        threading.Timer(5.0, _force_kill_if_alive).start()
    except Exception:
        swallowed()

