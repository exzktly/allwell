"""Run a Review-pane change behind a popup progress bar, one job at a time.

Every edit on the Cell Gating panel — a QC bound, an exclusion switch, the
fields-of-view picker, an intensity floor, a scatter-gate choice, an ON cutoff,
Auto-threshold, the optimizer — ends in work over every held well: a verdict
layer re-projected, the gating pass re-run, the CDFs re-read, a threshold scan.
Done on the GUI thread, that work froze the window for as long as it took, with
nothing on screen to say why. This module is the one route those edits take
instead:

* the **work** runs on a ``QThread`` and reports how far it has got;
* a :class:`widgets.progress_popup.ProgressPopup` shows that progress, and is
  application-modal so a second edit cannot be started against data the first
  is halfway through rewriting — modal, not frozen: the event loop keeps
  running, so the window repaints and the bar moves;
* the **finish** (labels, the CDF figure, the redraw) runs back on the GUI
  thread once the work has returned, delivered by a queued signal — a worker
  thread has no event loop, so neither a direct call nor
  ``QTimer.singleShot(0, …)`` from it would reach the GUI;
* jobs run **one at a time**, in the order they were asked for. A job submitted
  while another runs waits for it, so two jobs never write the cached frames at
  once. A job given a ``key`` replaces any job with the same key still waiting:
  only the newest of two waiting re-projections is worth running, since each
  reads the settings in force when it starts.

A job is either a work function (:func:`run_in_background`) or a ``start``
callable that launches a thread of its own and reports through the
:class:`JobHandle` it is handed (:func:`submit`) — the shape the gating pass
and Auto-threshold use, because their ``QThread`` classes have other callers
that show no popup.

**Cancelling.** A job offers Cancel only when cancelling is safe, which here
means the job *disowns* its result rather than leaving half of it applied:
Auto-threshold and the optimizer compute a number and apply it at the end, so a
cancelled one applies nothing. A re-projection or a gating pass rewrites the
wells one by one, so a cancelled one would leave some wells under the new
settings and the rest under the old, with the controls showing the new — a
degraded result indistinguishable from a good one — and those offer no Cancel.
A cancelled job still holds the queue until its thread has returned, behind a
"Cancelling…" popup, because a scan that cannot be interrupted is still
reading the frames the next job may write.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal, Slot

from core.safe import swallow

_logger = logging.getLogger("review.progress_jobs")


class JobReport:
    """What a work function is handed on its thread: report, and ask about cancel.

    Deliberately not the thread or a widget. A work function can say how far it
    has got and what it is doing, and ask whether the user has cancelled; it
    cannot touch anything on screen, which only the GUI thread may.
    """

    __slots__ = ("_thread",)

    def __init__(self, thread: _JobThread) -> None:
        self._thread = thread

    def progress(self, done: int, total: int) -> None:
        self._thread.progress.emit(int(done), int(total))

    def stage(self, text: str) -> None:
        self._thread.stage.emit(str(text))

    @property
    def cancelled(self) -> bool:
        return self._thread.cancelled


class _JobThread(QThread):
    """Call one work function on its own thread; report its outcome by signal."""

    progress = Signal(int, int)
    stage = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[JobReport], object]) -> None:
        super().__init__()
        self._work = work
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # QThread override
        try:
            result = self._work(JobReport(self))
        except Exception as exc:     # never raise off the GUI thread
            _logger.exception("background job failed")
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.succeeded.emit(result)


@dataclass
class _Job:
    title: str
    text: str
    start: Callable[[JobHandle], None]
    cancellable: bool
    key: str | None


class JobHandle(QObject):
    """The GUI-thread end of one running job.

    A job's thread connects its signals to these slots. The handle lives on the
    GUI thread, so every such connection is queued and every slot runs there —
    which is what lets them update the popup.
    """

    def __init__(self, runner: _Runner, job: _Job) -> None:
        super().__init__()
        self._runner = runner
        self.job = job
        self.cancelled = False
        self.ended = False
        #: Set by a job's ``start``: what to call when the user presses Cancel.
        self.on_cancel: Callable[[], None] | None = None
        #: Set by a job's ``start``: called on the GUI thread as the job ends,
        #: however it ends — the place for a cancelled job to put back what its
        #: start changed (a disabled button, say). ``cancelled`` says which.
        self.on_finish: Callable[[], None] | None = None
        #: The ``on_done`` / ``on_failed`` of a :func:`run_in_background` job.
        self.on_done: Callable[[object], None] | None = None
        self.on_failed: Callable[[str], None] | None = None

    @Slot(int, int)
    def progress(self, done: int, total: int) -> None:
        self._runner.show_progress(self, done, total)

    @Slot(str)
    def stage(self, text: str) -> None:
        self._runner.show_text(self, text)

    @Slot()
    def finish(self) -> None:
        """The job is over: close its popup and start the next one."""
        self._runner.finish(self)

    @Slot(object)
    def deliver(self, result) -> None:
        """A work function returned: run its GUI-thread finish, then end the job."""
        try:
            if not self.cancelled and self.on_done is not None:
                with swallow(f"finishing the background job {self.job.title!r}"):
                    self.on_done(result)
        finally:
            self.finish()

    @Slot(str)
    def deliver_failure(self, message: str) -> None:
        try:
            if not self.cancelled and self.on_failed is not None:
                with swallow(f"reporting the failed background job {self.job.title!r}"):
                    self.on_failed(message)
        finally:
            self.finish()


class _Runner(QObject):
    """The queue, the job in flight, and the popup it shows."""

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        self._queue: deque[_Job] = deque()
        self.current: JobHandle | None = None
        self.popup = None
        #: Job threads not yet returned, kept alive until they have: a
        #: ``QThread`` collected while running aborts the process.
        self._threads: set = set()

    # ── the queue ────────────────────────────────────────────────────────
    def submit(self, job: _Job) -> None:
        if job.key is not None:
            self._queue = deque(j for j in self._queue if j.key != job.key)
        self._queue.append(job)
        if self.current is None:
            self._start_next()

    def _start_next(self) -> None:
        if self.current is not None or not self._queue:
            return
        job = self._queue.popleft()
        handle = JobHandle(self, job)
        self.current = handle
        self._open_popup(job.title, job.text, cancellable=job.cancellable)
        try:
            job.start(handle)
        except Exception:
            _logger.exception("could not start the background job %r", job.title)
            self.finish(handle)

    def finish(self, handle: JobHandle) -> None:
        if handle.ended:
            return
        handle.ended = True
        if handle.on_finish is not None:
            with swallow(f"ending the background job {handle.job.title!r}"):
                handle.on_finish()
        if handle is self.current:
            self.current = None
            self._close_popup()
        self._start_next()

    def keep_thread(self, thread: QThread) -> None:
        self._threads.add(thread)
        thread.finished.connect(lambda t=thread: self._threads.discard(t))

    def stop(self) -> None:
        """Teardown: drop the queue, cancel the job in flight, close the popup."""
        self._queue.clear()
        handle, self.current = self.current, None
        if handle is not None:
            handle.cancelled = True
            handle.ended = True
            if handle.on_cancel is not None:
                with swallow("cancelling the background job at teardown"):
                    handle.on_cancel()
        self._close_popup()
        for thread in list(self._threads):
            with swallow("waiting on a background job thread at teardown"):
                if thread.isRunning():
                    thread.wait(250)

    # ── the popup ────────────────────────────────────────────────────────
    def _open_popup(self, title: str, text: str, *, cancellable: bool) -> None:
        from widgets.progress_popup import ProgressPopup

        self._close_popup()
        popup = ProgressPopup(self._parent_widget(), title=title, text=text,
                              cancellable=cancellable)
        if cancellable:
            popup.canceled.connect(self._on_cancel_pressed)
        popup.show()
        self.popup = popup

    def _close_popup(self) -> None:
        popup, self.popup = self.popup, None
        if _alive(popup):
            with swallow("closing the progress popup"):
                popup.close_popup()

    def _live_popup(self):
        """The popup, unless Qt has deleted it — with its parent window, say,
        which a job still reporting from its thread outlives at app close."""
        if not _alive(self.popup):
            self.popup = None
        return self.popup

    def _parent_widget(self):
        from PySide6.QtWidgets import QWidget
        return self._window if isinstance(self._window, QWidget) else None

    def _on_cancel_pressed(self) -> None:
        handle = self.current
        if handle is None or handle.cancelled:
            return
        handle.cancelled = True
        if handle.on_cancel is not None:
            with swallow(f"cancelling the background job {handle.job.title!r}"):
                handle.on_cancel()
        # Qt hid the popup when Cancel was pressed, and the job's thread may
        # take a while yet to return; until it has, say so rather than lift the
        # modality over a thread still reading the frames.
        self._open_popup(handle.job.title, f"Cancelling — {handle.job.title.lower()}…",
                         cancellable=False)

    def show_progress(self, handle: JobHandle, done: int, total: int) -> None:
        popup = self._live_popup()
        if handle is self.current and popup is not None and not handle.cancelled:
            popup.set_progress(done, total)

    def show_text(self, handle: JobHandle, text: str) -> None:
        popup = self._live_popup()
        if handle is self.current and popup is not None and not handle.cancelled:
            popup.set_text(text)


def _alive(obj) -> bool:
    """Whether *obj* is a Qt object whose C++ side still exists."""
    if obj is None:
        return False
    import shiboken6
    return shiboken6.isValid(obj)


def _runner(ctx) -> _Runner:
    slot = ctx.runtime.jobs
    runner = slot.runner
    if runner is None:
        runner = _Runner(getattr(ctx, "window", None))
        slot.runner = runner
        # App-close teardown reaches the runner the way it reaches every other
        # background job: a stopper parked on the runtime slice
        # (``core.worker_bridge.stop_runtime_callable``).
        slot.stop = runner.stop
    return runner


def _no_gui() -> bool:
    """True when no ``QApplication`` exists, so no popup or thread can be made."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() is None


# ── public API ───────────────────────────────────────────────────────────

def submit(ctx, *, title: str, text: str, start: Callable[[JobHandle], None],
           cancellable: bool = False, key: str | None = None) -> None:
    """Queue a job whose *start* launches its own work and reports through a handle.

    *start* runs on the GUI thread when the job's turn comes, with the popup
    already open. It must arrange for :meth:`JobHandle.finish` to be called when
    the work is over — connected to its thread's ``finished``, typically — and
    may set ``handle.on_cancel`` when *cancellable*.
    """
    _runner(ctx).submit(_Job(title=title, text=text, start=start,
                             cancellable=cancellable, key=key))


def run_in_background(ctx, *, title: str, text: str,
                      work: Callable[[JobReport], object],
                      on_done: Callable[[object], None],
                      on_failed: Callable[[str], None] | None = None,
                      cancellable: bool = False, key: str | None = None) -> None:
    """Queue *work(report)* for a thread, behind the popup; *on_done(result)* after.

    *work* runs off the GUI thread and must not touch a widget; it reports with
    ``report.progress(done, total)`` / ``report.stage(text)`` and, when
    *cancellable*, should return early once ``report.cancelled`` is true.
    *on_done* and *on_failed* run on the GUI thread, and neither runs for a job
    the user cancelled: a cancelled job's result is disowned.

    With no ``QApplication`` (a headless caller) there is no thread to hand the
    work to and no popup to show, so it runs inline and *on_done* follows it.
    """
    if _no_gui():
        try:
            result = work(_InlineReport())
        except Exception as exc:
            _logger.exception("background job %r failed", title)
            if on_failed is not None:
                on_failed(str(exc) or type(exc).__name__)
            return
        on_done(result)
        return

    def _start(handle: JobHandle) -> None:
        thread = _JobThread(work)
        handle.on_done = on_done
        handle.on_failed = on_failed
        handle.on_cancel = thread.cancel
        thread.progress.connect(handle.progress)
        thread.stage.connect(handle.stage)
        thread.succeeded.connect(handle.deliver)
        thread.failed.connect(handle.deliver_failure)
        _runner(ctx).keep_thread(thread)
        thread.start()

    submit(ctx, title=title, text=text, start=_start,
           cancellable=cancellable, key=key)


class _InlineReport:
    """The :class:`JobReport` of a job run inline: nothing to report to."""

    cancelled = False

    def progress(self, done: int, total: int) -> None:
        return None

    def stage(self, text: str) -> None:
        return None


def busy(ctx) -> bool:
    """Whether a job is running or waiting — data may be mid-rewrite."""
    slot = getattr(getattr(ctx, "runtime", None), "jobs", None)
    runner = getattr(slot, "runner", None) if slot is not None else None
    return bool(runner is not None and (runner.current is not None or runner._queue))


def stop_jobs(ctx) -> None:
    """App-close teardown: cancel what is running, drop what is waiting."""
    slot = getattr(getattr(ctx, "runtime", None), "jobs", None)
    runner = getattr(slot, "runner", None) if slot is not None else None
    if runner is not None:
        runner.stop()
