"""Keep the Mac awake for the length of a remote run.

A remote run is driven from this machine for its whole length: the app beats
the run's heartbeat, hands each batch to the driver and pulls every well home.
A Mac that goes to sleep stops all of it, and the cluster then reads the silence
as an app that died — between batches the node is handed back within
``UPLOAD_HEARTBEAT_STALE_S``, mid-batch the batch is suspended — so a plate left
running over lunch came back stopped (review C9).

:class:`SleepGuard` holds a power assertion with ``caffeinate`` for as long as
the run lasts: ``-i`` keeps the system from idle-sleeping, ``-s`` from sleeping
at all while on mains power, and ``-w <this app's pid>`` ends the assertion by
itself if the app goes away without releasing it. Closing the lid still sleeps
a Mac on battery; nothing a program can ask for prevents that. A no-op on every
other platform, and on a Mac without ``caffeinate``, which the run log then says.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable

from util.aw_safe import swallow


def _is_macos() -> bool:
    return sys.platform == "darwin"


def caffeinate_argv(executable: str, pid: int) -> list[str]:
    """The ``caffeinate`` command that holds the assertion for *pid*'s lifetime."""
    return [executable, "-i", "-s", "-w", str(pid)]


class SleepGuard:
    """``start`` holds the assertion, ``stop`` releases it; both are idempotent."""

    def __init__(self, log_fn: Callable[[str], None] | None = None) -> None:
        self._log = log_fn or (lambda _m: None)
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not _is_macos() or self._proc is not None:
            return
        exe = shutil.which("caffeinate")
        if exe is None:
            self._log("[remote] note: caffeinate is not available, so this Mac may sleep during the "
                      "run — and a sleeping Mac stops the run on the cluster.\n")
            return
        try:
            self._proc = subprocess.Popen(
                caffeinate_argv(exe, os.getpid()), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self._log(f"[remote] note: could not keep this Mac awake for the run ({exc}); "
                      f"a sleeping Mac stops the run on the cluster.\n")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with swallow("releasing the keep-awake assertion"):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def __enter__(self) -> "SleepGuard":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()
