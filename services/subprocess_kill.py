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
from typing import Optional


def signal_subprocess(proc: Optional[subprocess.Popen]) -> None:
    """Terminate ``proc`` and its whole process group.

    Safe to call when ``proc`` is None or already exited — both no-op.
    Schedules a SIGKILL escalation 5 seconds later in case the
    subprocess ignores SIGTERM (rare in practice, but
    multiprocessing-spawned workers occasionally do this during
    shutdown).
    """
    if proc is None or proc.poll() is not None:
        return

    pgid: Optional[int] = None
    if os.name == "posix":
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = None

    try:
        if os.name == "posix":
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        else:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    except (ProcessLookupError, OSError, AttributeError):
        proc.terminate()

    def _force_kill_if_alive() -> None:
        # Re-check the live process handle, not just the cached pgid —
        # if the process exited cleanly within the 5 s window, the OS
        # may have re-used the PID for an unrelated process.
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix" and pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            elif os.name == "posix":
                proc.kill()
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass

    threading.Timer(5.0, _force_kill_if_alive).start()
