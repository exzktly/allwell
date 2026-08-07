"""An edit session: a tool's page standing in for a queued job.

The Job Queue borrows a tool's own page rather than duplicating its form. A
row's *Edit…* populates that page from the job's stored spec and puts a banner
on it; Save collects the page straight back over the job. Adding a new job of
any kind is the same mechanism with no job to write back to yet.

Everything about *when that is allowed* lives here, and it is more than it
looks. Three of the rules exist because their absence silently corrupted the
job being edited:

* A session may not open while that tool has a run of its own in flight —
  populating a page mid-run overwrites the fields its controller is driving as
  a progress display, and the page is what Save collects.
* A session must be abandoned if its job starts running or leaves the queue,
  or Save would write over a spec already handed to a runner, or to a job that
  no longer exists.
* Opening a second session says the first was closed unsaved, rather than
  replacing it while its banner vanishes from a page the user has left.

Deliberately Qt-free in its own right: it holds no widgets and imports no Qt.
It reaches the outside through three injected callables — ``notify`` for
anything it refuses, and ``on_session`` / ``on_session_end``, which
``DatasetsTab`` supplies as "show that tool's page with this banner" and "take
the banner off". That keeps the one ``notify`` seam the tests assert against,
and lets the whole class be driven in a test with no page at all.
"""

from __future__ import annotations

from collections.abc import Callable

from services.job_kinds import KIND_LABEL
from services.job_queue import JobSpec, JobState
from well_viewer.core.safe import swallowed


class QueueSession:
    """Which tool page, if any, is currently standing in for a queued job."""

    def __init__(
        self,
        bridge,
        *,
        notify: Callable[[str, str], None],
        add_job: Callable[[str, dict], None],
    ) -> None:
        self._bridge = bridge
        self._notify = notify
        self._add_job = add_job
        self._collectors: dict[str, Callable[[], dict]] = {}
        self._on_session: Callable[[str, str], None] | None = None
        self._on_session_end: Callable[[str], None] | None = None
        # (kind, job_id or None) — None means "a new job of this kind".
        self._session: tuple[str, str | None] | None = None

    # ── wiring ───────────────────────────────────────────────────────────
    def register_collector(self, kind: str, collect: Callable[[], dict]) -> None:
        """How this session reads a tool's page back — the tool's own
        ``collect_spec``, which is also what its "+ Queue" button uses."""
        self._collectors[kind] = collect

    def set_hooks(
        self,
        on_session: Callable[[str, str], None],
        on_session_end: Callable[[str], None],
    ) -> None:
        """``DatasetsTab`` supplies "show that tool's page with this banner on
        it" and "take the banner off again"; this class supplies everything
        about what the session means."""
        self._on_session = on_session
        self._on_session_end = on_session_end

    def current(self) -> tuple[str, str | None] | None:
        """The live session, as ``(kind, job_id or None)``."""
        return self._session

    # ── opening ──────────────────────────────────────────────────────────
    def begin_edit(self, job_id: str) -> bool:
        """Open *job_id* on its own page for editing."""
        job = self._bridge.queue.get(job_id)
        if job is None:
            return False
        if job.state == JobState.RUNNING:
            self._notify("That job is running — stop it first to edit it.", "warn")
            return False
        if self._bridge.runner_busy(job.kind):
            self._notify(f"{KIND_LABEL.get(job.kind, job.kind)} is busy with its "
                         "own run — try again when it finishes.", "warn")
            return False
        problems = self._bridge.hydrate_job(job_id)
        if problems is None:
            self._notify("That job's page isn't available to edit.", "warn")
            return False
        problems += self._round_trip_losses(job)
        message = f"Editing queued job “{job.label}”."
        if job.state in JobState.TERMINAL:
            # update_spec deliberately leaves state alone, so saving this
            # changes what the job says it will do without putting it back in
            # the queue. Said here rather than discovered after pressing Save.
            message += (" This job has already finished — saving updates it, "
                        "but it won't run again until you choose Retry.")
        for problem in problems:
            self._notify(problem, "warn")
        self._start(job.kind, job_id, message)
        return True

    def begin_new_job(self, kind: str) -> bool:
        """Open *kind*'s page to set up a job that does not exist yet."""
        if self._bridge.runner_busy(kind):
            self._notify(f"{KIND_LABEL.get(kind, kind)} is busy with its own run.",
                         "warn")
            return False
        if kind not in self._collectors:
            self._notify(f"{KIND_LABEL.get(kind, kind)} isn't available in this build.",
                         "warn")
            return False
        self._start(kind, None,
                    f"Setting up a new {KIND_LABEL.get(kind, kind)} job. "
                    "Fill the page in, then add it to the queue.")
        return True

    def _round_trip_losses(self, job: JobSpec) -> list[str]:
        """Which of the job's settings the page could not take on.

        A tool's ``show_job`` is only as faithful as the page can be right
        now. Subset's, for instance, populates its timepoint and FOV filters
        from checkboxes it builds by reading the source dataset — so if that
        dataset has moved or not been built yet, the boxes are never created,
        ``all_timepoints_selected()`` answers "yes" over an empty list, and
        collecting the page back turns a one-timepoint subset into every
        timepoint. That is a widening, and it is invisible.

        Rather than special-casing each tool, the session collects the page it
        just populated and reports any setting that was there and is now not.
        A value that merely changed form (a well relabelled ``A1`` → ``A01``)
        is not a loss and is not reported.
        """
        collect = self._collectors.get(job.kind)
        if collect is None:
            return []
        try:
            round_tripped = collect()
        except Exception:
            # A page too empty to collect from cannot report a loss either;
            # begin_edit's own guards already refused the cases that matter.
            swallowed("collecting a page to check an edit round-trip")
            return []
        lost = [key for key, value in (job.spec or {}).items()
                if value and not round_tripped.get(key)]
        if not lost:
            return []
        return ["This page couldn't show every setting on that job "
                f"({', '.join(sorted(lost))}) — saving would clear "
                "them. Check the input path first."]

    def _start(self, kind: str, job_id: str | None, message: str) -> None:
        if self._session is not None and self._session != (kind, job_id):
            # Silently overwriting it discarded whatever the user had typed on
            # the other page, with the banner disappearing from a page they
            # were no longer looking at.
            self._notify("Your previous edit was closed without saving.", "warn")
        self._session = (kind, job_id)
        if self._on_session is not None:
            self._on_session(kind, message)

    # ── closing ──────────────────────────────────────────────────────────
    def save(self) -> bool:
        """Collect the session's page and either update its job or add one."""
        if self._session is None:
            return False
        kind, job_id = self._session
        collect = self._collectors.get(kind)
        if collect is None:
            self._notify(f"{KIND_LABEL.get(kind, kind)} can't be saved to the "
                         "queue in this build — nothing was changed.", "warn")
            self.end()
            return False
        spec = collect()
        if job_id is None:
            self._add_job(kind, spec)
        elif not self._bridge.update_job(job_id, spec):
            # The job started running, or left the queue, between opening the
            # session and saving it. Silence here reads as a successful save.
            self._notify("That job changed while you were editing it — your "
                         "changes were not saved.", "warn")
            self.end()
            return False
        self.end()
        return True

    def end(self) -> None:
        self._session = None
        if self._on_session_end is not None:
            self._on_session_end("")

    def abandon_if_stale(self) -> None:
        """Drop a session whose job started running or left the queue.

        Both would make saving it meaningless — one writes over a spec already
        handed to a runner, the other writes to a job that no longer exists —
        and the banner would otherwise sit there offering to do it.
        """
        if self._session is None:
            return
        _kind, job_id = self._session
        if job_id is None:
            return
        job = self._bridge.queue.get(job_id)
        if job is None:
            self._notify("The job you were editing was removed.", "warn")
            self.end()
        elif job.state == JobState.RUNNING:
            self._notify("The job you were editing has started running.", "warn")
            self.end()

    def blocks_preview_of(self, kind: str, job_id: str) -> bool | None:
        """Whether a row click on *job_id* must be refused.

        ``True`` means refuse and say so, ``None`` means the click is a no-op
        (that job is already the one open), and ``False`` means let it through.
        Preview writes the clicked job's fields into the same page the session
        has open, so allowing it would collect *that* job's settings into the
        one under edit.
        """
        if self._session is None or self._session[0] != kind:
            return False
        return None if self._session[1] == job_id else True
