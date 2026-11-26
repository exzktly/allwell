"""Polls a watched folder off the UI thread until it settles.

The decision itself is :class:`datasets.watch_folder.watch_folder_compute.SettleTracker`;
this thread only takes a snapshot, hands it over, reports what came back and
sleeps. Listing a large folder on a network share can take seconds, which is
why it is not done on a ``QTimer`` on the UI thread.

Sleeping is done in short slices so a Stop is answered within a fraction of a
second rather than at the end of a five-second interval.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.watch_folder.watch_folder_compute import (
    DEFAULT_POLL_SECONDS,
    SETTLED,
    SettleTracker,
    poll_interval,
    snapshot,
)

#: The longest the thread sleeps before checking for a Stop again, in ms.
_SLEEP_SLICE_MS = 100

CANCELLED_PREFIX = "Cancelled"


class WatchFolderWorker(QThread):
    """Watches one folder; emits ``succeeded`` once it has settled."""

    logLine = Signal(str)
    #: The latest :class:`WatchStatus`, once per poll.
    status = Signal(object)
    succeeded = Signal(str, str)     # (folder, summary)
    failed = Signal(str)

    def __init__(self, folder: str, *, quiet_seconds: float, require_change: bool,
                 give_up_seconds: float = 0.0,
                 poll_seconds: float = DEFAULT_POLL_SECONDS, parent=None) -> None:
        super().__init__(parent)
        self._folder = folder
        self._tracker = SettleTracker(quiet_seconds, require_change=require_change)
        self._give_up = max(0.0, float(give_up_seconds))
        self._poll = max(0.01, float(poll_seconds))

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            self._watch()
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("watch folder poll")
            self.failed.emit(f"Watching stopped on an error: {exc}")

    def _watch(self) -> None:
        began = time.monotonic()
        last_phase = ""
        while not self.isInterruptionRequested():
            scan_began = time.monotonic()
            snap = snapshot(self._folder)
            now = time.monotonic()
            status = self._tracker.observe(snap, now)
            self.status.emit(status)
            if status.phase != last_phase:
                self.logLine.emit(status.sentence())
                last_phase = status.phase
            if status.phase == SETTLED:
                self.succeeded.emit(self._folder, status.sentence())
                return
            if self._give_up and now - began >= self._give_up:
                hours = self._give_up / 3600.0
                self.failed.emit(
                    f"Gave up after {hours:g} h without the folder settling — "
                    f"{status.sentence()}")
                return
            self._sleep(poll_interval(self._poll, now - scan_began))
        self.failed.emit(f"{CANCELLED_PREFIX} — stopped watching {self._folder}.")

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self.isInterruptionRequested():
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self.msleep(int(min(_SLEEP_SLICE_MS, left * 1000)) or 1)
