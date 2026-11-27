"""Wires the Watch folder page to its polling worker and to the Job Queue.

A watch is a queueable tool like the others (``QueueableController``): it
starts from a queued spec, reports one ``jobFinished`` per run, and its
reported path is the folder it watched, so a job chained after it reads that
folder. What it does *not* do is work this computer hard — it lists a folder
every few seconds — which is why it runs in a lane of its own
(``services.job_lanes.WATCH``) rather than holding the local lane, where it
would keep every conversion queued behind it waiting for the whole
acquisition.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from util.aw_safe import swallowed
from datasets.core.tool_controller import DatasetToolController
from datasets.watch_folder.view import WatchFolderPage
from datasets.watch_folder.watch_folder_compute import (
    DEFAULT_POLL_SECONDS,
    DEFAULT_QUIET_MINUTES,
    spec_quiet_seconds,
)
from datasets.watch_folder.worker import WatchFolderWorker

KIND = "watch_folder"


def _epoch(value: object) -> float | None:
    """*value* as epoch seconds, or ``None`` when it is not a number."""
    return float(value) if isinstance(value, (int, float)) else None


class WatchFolderController(DatasetToolController):
    """Behaviour for the Watch folder feature."""

    _shutdown_label = "watch folder"

    #: The countdown, for the watch's row in the queue
    #: (``QueueBridge._on_status_text``).
    statusText = Signal(str)

    #: What a paused watch's row says (``QueueBridge.suspend_current``). A
    #: watch has nothing to finish: it stops looking, and starting the queue
    #: again watches afresh.
    #: The queue hands a resumed watch its first start time as ``armed_at``
    #: (``QueueBridge._dispatch``), so files written while it was paused or the
    #: app was closed count as the change it waits for.
    resumes_from_first_start = True

    suspending_message = "pausing — stopping the watch"
    suspended_message = ("paused — watches the folder again when the queue "
                         "starts, counting files written in the meantime")

    def __init__(
        self,
        *,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_add_to_queue = on_add_to_queue
        self._worker: WatchFolderWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")
        #: The poll interval, in seconds. Not on the page — a person has no
        #: reason to change it — but a spec may carry ``poll_seconds``, which
        #: is how a test watches a folder without waiting five seconds a look.
        self._poll_seconds = DEFAULT_POLL_SECONDS
        #: Set by :meth:`suspend` for the run it stopped, so the queue can tell
        #: a Pause from a Stop (``QueueBridge._was_suspended``).
        self._suspended = False

        self.page = WatchFolderPage()
        self.page.runRequested.connect(lambda: self._launch())
        self.page.queueRequested.connect(self._on_queue_requested)

    # ── run ──────────────────────────────────────────────────────────────────
    def _launch(self, spec: dict | None = None) -> None:
        """Start watching. *spec* is a queued job's own settings; without one
        (Start watching on the page) the page's are used. A queued job's are
        used as stored rather than read back off the page, whose spin boxes
        round to what they display."""
        if self._worker is not None and self._worker.isRunning():
            return
        folder = self.page.folder_path()
        if not folder:
            message = "No folder to watch selected."
            self.page.append_log(f"✗ {message}")
            self.page.set_status_text(message, failed=True)
            self._last_result = (False, message, "")
            # No worker starts on this path, so _on_finished never fires: a
            # queued job would sit "running" for ever without this.
            try:
                self.jobFinished.emit(False, message, "")
            except Exception:
                swallowed()
            return
        self._suspended = False
        self.page.clear_log()
        self.page.set_running(True)
        spec = {**self.collect_spec(), **(spec or {}), "folder": folder}
        worker = WatchFolderWorker(
            folder,
            quiet_seconds=spec_quiet_seconds(spec),
            require_change=bool(spec.get("require_change", True)),
            give_up_seconds=float(spec.get("give_up_hours") or 0.0) * 3600.0,
            poll_seconds=self._poll_seconds,
            armed_at=_epoch(spec.get("armed_at")),
        )
        worker.logLine.connect(self.page.append_log)
        worker.status.connect(self.page.show_status)
        worker.status.connect(lambda status: self.statusText.emit(status.sentence()))
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self.page.append_log(f"Watching {folder}")
        self._start_worker(worker)

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {
            "folder": self.page.folder_path(),
            "quiet_minutes": self.page.quiet_minutes(),
            "require_change": self.page.require_change(),
            "give_up_hours": self.page.give_up_hours(),
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue(KIND, self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_quiet_minutes(float(spec.get("quiet_minutes", DEFAULT_QUIET_MINUTES)))
        self.page.set_require_change(bool(spec.get("require_change", True)))
        self.page.set_give_up_hours(float(spec.get("give_up_hours") or 0.0))
        self.page.set_folder_path(str(spec.get("folder", "")))

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        poll = spec.get("poll_seconds")
        self._poll_seconds = float(poll) if poll else DEFAULT_POLL_SECONDS
        self._launch(spec)

    def suspend(self) -> None:
        """Pause, as the queue's Pause asks of every runner that can.

        Without this the queue fell back to a plain Stop, which marks a watch
        cancelled and blocks every job chained after it — so pausing the queue
        to hold back local work threw away a watch that may have been running
        for hours, and the whole chain had to be retried. A watch keeps no
        progress, so suspending is stopping and being picked up again.
        """
        if self.is_busy():
            self._suspended = True
            self.stop()

    def was_suspended(self) -> bool:
        return self._suspended

    # ── worker results ───────────────────────────────────────────────────────
    def _on_succeeded(self, folder: str, summary: str) -> None:
        self.page.append_log(f"✓ {summary}")
        self.page.set_status_text(summary)
        self._last_result = (True, summary, folder)

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        self.page.set_status_text(message, failed=True)
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        self._poll_seconds = DEFAULT_POLL_SECONDS
        ok, message, folder = self._last_result
        try:
            self.jobFinished.emit(ok, message, folder)
        except Exception:
            swallowed()
