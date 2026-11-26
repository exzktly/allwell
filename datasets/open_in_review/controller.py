"""Wires the Open in Review page to the shell, and to the Job Queue as a step.

There is no worker: handing a folder to Review is a request the shell carries
out on its next event-loop turn (``AllWellApp._show_result_in_review``). So a
queued step finishes as soon as the hand-off is made, and ``jobFinished`` is
emitted directly, as the last statement of the run, as every queue runner
must.

A queued step asks the shell to move the window to Review only when nobody is
using the app; pressing **▶ Open in Review** on the page always moves it,
since the person pressing it is right there.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject

from util.aw_safe import swallowed
from datasets.core.tool_controller import DatasetToolController
from datasets.open_in_review.open_in_review_compute import (
    OUTCOME_MESSAGES,
    not_reviewable_reason,
    reviewable_root,
)
from datasets.open_in_review.view import OpenInReviewPage

KIND = "open_in_review"


class OpenInReviewController(DatasetToolController):
    """Behaviour for the Open in Review feature."""

    _shutdown_label = "open in review"

    def __init__(
        self,
        *,
        show_in_review: Callable[..., str] | None = None,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._show = show_in_review
        self._on_add_to_queue = on_add_to_queue
        self._worker = None

        self.page = OpenInReviewPage()
        self.page.runRequested.connect(lambda: self._run(queued=False))
        self.page.queueRequested.connect(self._on_queue_requested)

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {"dataset": self.page.dataset_path()}

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue(KIND, self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_dataset_path(str(spec.get("dataset", "")))

    def set_pending_inputs(self, pending: dict, projection: dict | None = None) -> None:
        """Show the dataset as a queued job's output not written yet.
        *projection* is unused: nothing here is built from its contents."""
        self.page.set_pending_inputs(pending)

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        self._run(queued=True)

    # ── run ──────────────────────────────────────────────────────────────────
    def _run(self, *, queued: bool) -> None:
        path = self.page.dataset_path()
        root = reviewable_root(path)
        if root is None:
            self._finish(False, not_reviewable_reason(path), "")
            return
        if self._show is None:
            self._finish(False, "Review isn't available in this build.", "")
            return
        outcome = self._show(Path(root), when_idle=queued)
        message = OUTCOME_MESSAGES.get(outcome, "Review isn't available right now.").format(
            root=root)
        if outcome == "held":
            self._toast(message)
        self._finish(outcome in OUTCOME_MESSAGES, message, str(root))

    def _toast(self, message: str) -> None:
        from widgets.toast import Toast

        try:
            Toast.show_message(self.page.window(), message, kind="warn", msec=8000)
        except RuntimeError:
            swallowed("open in review toast")

    def _finish(self, ok: bool, message: str, root: str) -> None:
        self.page.append_log(("✓ " if ok else "✗ ") + message)
        self.page.set_status_text(("✓ " if ok else "✗ ") + message)
        try:
            self.jobFinished.emit(ok, message, root)
        except Exception:
            swallowed()
