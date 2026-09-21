"""The worker lifecycle every Datasets tool controller shares.

The Datasets tool controllers each declared the same ``jobFinished`` signal and
the same three methods for owning a background worker: ``stop``, ``is_busy``,
and a ``shutdown`` that races the worker to a halt before the app tears down.
The bodies were identical bar two things — which method asks the worker to stop, and which label
a swallowed teardown error is logged under — so they lived as six copies of one
decision, which is the shape §6b describes the log furniture having before
``LogPanelMixin``.

**What is deliberately *not* here.** ``_on_run``, ``_on_queue_requested``,
``_on_succeeded``, ``_on_failed`` and ``_on_finished`` look like siblings and are
not. Measured across the seven by hashing each one's normalised body,
``_on_queue_requested`` has seven distinct bodies and ``_on_finished`` five,
while ``_on_succeeded``'s signature diverges outright (Validate's takes a
``DatasetReport``). Pulling them up would mean a base full of flags and
overrides that says less than the seven plain methods do. The three
copy-into-a-destination tools do share a run-and-confirm-restart cluster; that
belongs to those three (see :class:`DestinationToolController`), not to all six.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from util.aw_safe import swallowed

_logger = logging.getLogger("review")


class DatasetToolController(QObject):
    """Base for a Datasets tool controller that owns one background worker.

    A subclass keeps a ``self._worker`` (``None`` when idle) and sets
    :attr:`_shutdown_label`. One that cannot be stopped by Qt's interruption
    flag overrides :meth:`_request_worker_stop`.
    """

    #: QueueableController hook: ``(ok, message, dest_root — "" for none)``.
    jobFinished = Signal(bool, str, str)

    #: Named in the DEBUG breadcrumb if the teardown race raises.
    _shutdown_label = "datasets tool"

    def _request_worker_stop(self, worker) -> None:
        """Ask *worker* to stop. Qt's cooperative flag, unless overridden.

        ``import_loose`` overrides this, and the reason is worth stating where
        the default lives: its worker spends the run blocked inside a
        subprocess's stdout read loop and never polls
        ``isInterruptionRequested()``, so the flag alone is a no-op there and
        the tracked child process would outlive the app.
        """
        worker.requestInterruption()

    def stop(self) -> None:
        """QueueableController hook: cancel the run in flight, if any."""
        if self._worker is not None and self._worker.isRunning():
            self._request_worker_stop(self._worker)

    def is_busy(self) -> bool:
        """QueueableController hook: whether a run is already in flight.

        Checked by ``QueueBridge.show_job`` before it repopulates a page from a
        clicked queued row, since ``show_job()`` writes fields directly —
        bypassing ``set_running``'s field locks, which only block user clicks —
        and a running tool may be actively painting progress onto the very
        widgets those fields feed.
        """
        return self._worker is not None and self._worker.isRunning()

    def shutdown(self) -> None:
        """Stop a running worker before teardown (called on app close).

        The ``RuntimeError`` guard is for the worker's C++ object having already
        gone; there is nothing left to stop in that case, and a traceback on the
        way out of the app helps nobody — but it still leaves a breadcrumb
        rather than passing silently (§15).

        A worker still running when the two seconds are up is **not** waited on
        further. Quitting is the user asking for their machine back, and the
        jobs this owns — a plate copy, a merge across a network mount — will not
        finish inside any deadline worth holding a quit for. But it is said out
        loud, because of what happens next: ``AllWellApp.closeEvent`` goes
        straight on to tear down the Review pane and then the window, so a
        worker that outlives this call is emitting into a widget tree being
        deleted. That is the shape that ends in a native crash with no Python
        traceback, and this line is the only thing that will name it in the
        boot log afterwards.
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        try:
            self._request_worker_stop(worker)
            if not worker.wait(2000):
                _logger.warning(
                    "%s worker did not stop within 2s; quitting anyway — if the "
                    "app crashes on exit, this is the thread that was still "
                    "running.", self._shutdown_label,
                )
        except RuntimeError:
            swallowed(f"{self._shutdown_label} worker shutdown")


class DestinationToolController(DatasetToolController):
    """A tool that copies a dataset into a destination folder the user names.

    Subset, Merge Plates and Concatenate share more than the worker lifecycle:
    each has a Run button that must first ask about resuming into a folder
    holding an interrupted attempt, and an "Open in Review" button that hands
    the finished folder back to the shell. Those four members were identical
    across the three, bar one docstring.

    A subclass provides :meth:`_destination_has_marker` — the predicate bound to
    its *own* in-progress marker, since a folder carrying another tool's
    leftovers is not a resume — and keeps ``self.page`` and
    ``self._on_open_in_review``.
    """

    @staticmethod
    def _destination_has_marker(dest) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_run(self) -> None:
        dest_path = self.page.destination_path()
        if dest_path and self._destination_is_restart(dest_path) and not self._confirm_restart(dest_path):
            return
        self._launch()

    def _destination_is_restart(self, dest_path: str) -> bool:
        """True if *dest_path* holds this tool's own unfinished attempt."""
        from pathlib import Path

        from datasets.core.dataset_paths import DatasetPaths

        root = Path(dest_path)
        return self._destination_has_marker(
            DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out"))

    def _confirm_restart(self, dest_path: str) -> bool:
        """Destructive-clear confirmation before resuming into a destination
        that holds leftovers from an interrupted attempt — the content is
        rebuilt from scratch rather than added to, so the user has to say so
        (see each tool's ``destination_has_marker`` / ``apply.clear_destination``)."""
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self.page,
            "Resume interrupted run?",
            "The destination already has partial output from an earlier "
            f"attempt that didn't finish:\n\n{dest_path}\n\n"
            "Continuing will delete that partial content and rebuild it from "
            "scratch. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _on_open_in_review_requested(self, dest_root: str) -> None:
        if self._on_open_in_review is not None:
            self._on_open_in_review(dest_root)
