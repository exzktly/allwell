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

import os
import uuid
from pathlib import Path
from collections.abc import Callable

from services.job_kinds import (
    CHAIN_KIND,
    KIND_LABEL,
    describe,
    input_label,
    input_targets,
    planned_output,
)
from services.job_queue import ChainLink, JobSpec, JobState, set_dotted
from core.safe import swallowed
from datasets.job_queue.projection import project


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
        self._validators: dict[str, Callable[[], bool]] = {}
        self._on_session: Callable[[str, str], bool | None] | None = None
        self._on_session_end: Callable[[str], None] | None = None
        # (kind, job_id or None) — None means "a new job of this kind".
        self._session: tuple[str, str | None] | None = None
        # The session's inputs that come from earlier jobs: target ->
        # (source job id, the path the page was given for it). Save links a
        # target only while the page still holds that path, which is how a
        # Browse to a real folder drops the link and a Swap swaps it.
        self._links: dict[str, tuple[str, str]] = {}

    # ── wiring ───────────────────────────────────────────────────────────
    def register_collector(self, kind: str, collect: Callable[[], dict]) -> None:
        """How this session reads a tool's page back — the tool's own
        ``collect_spec``, which is also what its "+ Queue" button uses."""
        self._collectors[kind] = collect

    def register_validator(self, kind: str, validate: Callable[[], bool]) -> None:
        """How this session asks a tool's page whether it is fit to queue.

        Optional, and only Analyze has one. Its "+ Queue" button refuses a
        form that its Run button would refuse — no input folder, an
        unparseable schema, an out-of-range number — and the banner's Save is
        the same act by another route. Without the check here, Save collected
        whatever the form held and queued it, and the job failed at dequeue
        time with nobody watching. The validator reports its own reason (Analyze
        names the offending field); a ``False`` keeps the session open so the
        user can correct the page rather than losing it.
        """
        self._validators[kind] = validate

    def set_hooks(
        self,
        on_session: Callable[[str, str], bool | None],
        on_session_end: Callable[[str], None],
    ) -> None:
        """``DatasetsTab`` supplies "show that tool's page with this banner on
        it" and "take the banner off again"; this class supplies everything
        about what the session means.

        *on_session* may answer ``False`` to say the page could not be put in
        front of the user; the session is then dropped and the refusal
        reported, rather than left open behind a banner nobody can see."""
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
        started = self._start(job.kind, job_id, message)
        if started:
            self._links = {link.target: (link.source, _dotted(job.spec, link.target))
                           for link in job.links() if link.target}
            self._show_links(job.kind)
        return started

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
        return self._start(kind, None,
                           f"Setting up a new {KIND_LABEL.get(kind, kind)} job. "
                           "Set every option on the page, then add it to the queue.")

    def begin_next_step(self, kind: str, sources: dict[str, str]) -> bool:
        """Open *kind*'s page to set up a job that takes its inputs from
        earlier jobs' outputs — "Analyze the output of that Concatenate".

        *sources* maps each of the new job's input targets to the id of the
        job whose output fills it. The page starts from a blank job holding
        only those inputs, each showing the folder its source is set up to
        write and that it is not written yet; everything else is set as for
        any new job. Saving adds the job **with** its links, so a running
        queue cannot start it against the planned paths first.
        """
        queue = self._bridge.queue
        targets = input_targets(kind)
        label = KIND_LABEL.get(kind, kind)
        if not sources or any(t not in targets for t in sources):
            self._notify(f"{label} can't take its input from that job.", "warn")
            return False
        if any(queue.get(s) is None for s in sources.values()):
            self._notify("That job has left the queue.", "warn")
            return False
        if self._bridge.runner_busy(kind):
            self._notify(f"{label} is busy with its own run.", "warn")
            return False
        if kind not in self._collectors:
            self._notify(f"{label} isn't available in this build.", "warn")
            return False
        links = {target: (source, self._source_path(source))
                 for target, source in sources.items()}
        skeleton: dict = {}
        for target, (_source, path) in links.items():
            set_dotted(skeleton, target, path)
        if not self._bridge.show_spec(kind, skeleton):
            self._notify(f"{label}'s page isn't available right now.", "warn")
            return False
        names = " and ".join(f"“{queue.get(s).label or s}”" for s in dict.fromkeys(sources.values()))
        started = self._start(kind, None, (
            f"Setting up a queued {label} job that runs after {names}. Inputs marked ⧗ "
            "are read from those jobs' outputs when it runs. Set every other "
            "option on the page, then add it to the queue."))
        if started:
            self._links = links
            self._show_links(kind)
        return started

    def _source_path(self, source_id: str) -> str:
        """The folder a source job wrote, or is set up to write."""
        source = self._bridge.queue.get(source_id)
        if source is None:
            return ""
        if source.state == JobState.SUCCEEDED and source.dataset_root:
            return source.dataset_root
        return planned_output(source.kind, source.spec) or ""

    def _show_links(self, kind: str) -> None:
        """Tell the page which inputs are other jobs' outputs, and what the
        first of those is expected to hold — a Subset builds its pickers from
        it, an Analyze its channels."""
        if not self._links:
            # A job with no links has nothing to show, and telling the page
            # "nothing is pending" is not free: a page that rebuilds itself on
            # it would discard what the edit just loaded into it.
            return
        queue = self._bridge.queue
        pending: dict[str, str] = {}
        projection = None
        for target in input_targets(kind):
            if target not in self._links:
                continue
            source = queue.get(self._links[target][0])
            if source is None:
                continue
            if source.state != JobState.SUCCEEDED:
                pending[target] = pending_note(source, kind, target)
            if projection is None:
                projection = project(queue, source.id).as_dict()
        self._bridge.show_pending(kind, pending, projection)

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

    def _start(self, kind: str, job_id: str | None, message: str) -> bool:
        if self._links and self._session is not None:
            # The previous session's inputs stop being anyone's output.
            self._bridge.show_pending(self._session[0], {}, None)
        self._links = {}
        if self._session is not None and self._session != (kind, job_id):
            # Silently overwriting it discarded whatever the user had typed on
            # the other page, with the banner disappearing from a page they
            # were no longer looking at.
            self._notify("Your previous edit was closed without saving.", "warn")
        self._session = (kind, job_id)
        if self._on_session is None:
            return True
        if self._on_session(kind, message) is False:
            # Analyze lives in another pane, reachable only through the shell.
            # A session whose page never appeared is a click that did nothing,
            # which is exactly how "+ Add job → Analyze" used to behave.
            self._session = None
            self._notify(f"Couldn't open the {KIND_LABEL.get(kind, kind)} page "
                         "from here — switch to it yourself.", "warn")
            return False
        return True

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
        validate = self._validators.get(kind)
        if validate is not None and not validate():
            return False        # the page said why; the session stays open
        spec = collect()
        kept = self._kept_links(spec)
        if job_id is None and kept:
            job = JobSpec(id=uuid.uuid4().hex[:10], kind=kind,
                          label=describe(kind, spec), spec=spec)
            refused = self._bridge.add_job(job, links=kept)
            self._report_refused(kind, refused)
        elif job_id is None:
            self._add_job(kind, spec)
        elif not self._bridge.update_job(job_id, spec):
            # The job started running, or left the queue, between opening the
            # session and saving it. Silence here reads as a successful save.
            self._notify("That job changed while you were editing it — your "
                         "changes were not saved.", "warn")
            self.end()
            return False
        else:
            self._relink(job_id, kind, kept)
        self.end()
        return True

    def _kept_links(self, spec: dict) -> list[ChainLink]:
        """The session's links whose input still holds the path it was given.

        Matched by value rather than by field, so the two ways a user changes
        an input both do the right thing: Browse-ing a real folder replaces
        the path and drops that link, and swapping two inputs moves each path
        — and so each link — to the other target.
        """
        kind = self._session[0] if self._session else ""
        kept: list[ChainLink] = []
        claimed: set[str] = set()
        for target in input_targets(kind):
            value = _dotted(spec, target)
            for source_target, (source, path) in self._links.items():
                if source_target in claimed or not _same_path(value, path):
                    continue
                kept.append(ChainLink(source, target, CHAIN_KIND))
                claimed.add(source_target)
                break
        return kept

    def _relink(self, job_id: str, kind: str, kept: list[ChainLink]) -> None:
        """Make an edited job's links exactly the kept ones: a link whose
        input was given another folder is dropped, a swapped one moved."""
        wanted = {link.target: link.source for link in kept}
        job = self._bridge.queue.get(job_id)
        current = {link.target: link.source for link in job.links()} if job else {}
        refused = []
        for target in input_targets(kind):
            if wanted.get(target) == current.get(target):
                continue
            if not self._bridge.set_link(job_id, target, wanted.get(target, "")):
                refused.append(target)
        self._report_refused(kind, refused)

    def _report_refused(self, kind: str, targets: list[str]) -> None:
        if targets:
            names = ", ".join(input_label(kind, t) for t in targets)
            self._notify(f"Couldn't keep the link for {names} — that input "
                         "uses the folder shown on the page instead.", "warn")

    def end(self) -> None:
        kind = self._session[0] if self._session else None
        had_links = bool(self._links)
        self._session = None
        self._links = {}
        if kind is not None and had_links:
            # The page is left as an ordinary page again: nothing marked as a
            # queued job's output once no session is about to link it.
            self._bridge.show_pending(kind, {}, None)
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
        gone = [source for source, _path in self._links.values()
                if self._bridge.queue.get(source) is None]
        if gone:
            # Saving would add the job with that input unlinked, and a running
            # queue could start it at once against a folder nothing will write.
            self._notify("A job this one was set up to read from was removed — "
                         "the setup was closed without saving.", "warn")
            self.end()
            return
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


def pending_note(source: JobSpec, kind: str, target: str) -> str:
    """The sentence an input field shows while it waits on *source*."""
    what = (f"{input_label(kind, target)}: output" if len(input_targets(kind)) > 1
            else "Output")
    return (f"⧗ {what} of queued job “{source.label or source.id}” — not "
            "written yet; filled in when that job finishes.")


def _same_path(a: str, b: str) -> bool:
    """Whether two spellings name the same folder.

    A path makes a round trip through each page's form before it is
    collected, and the form may respell it: Analyze stores ``Path(text)``,
    which on Windows turns ``C:/data/plate`` into ``C:\\data\\plate`` and
    everywhere drops a trailing separator. Compared as raw strings, those
    dropped the link without a word.
    """
    if not a or not b:
        return a == b
    return (os.path.normcase(os.path.normpath(str(Path(a))))
            == os.path.normcase(os.path.normpath(str(Path(b)))))


def _dotted(spec: dict, dotted: str | None) -> str:
    """The string at *dotted* in *spec*, or ``""``."""
    cur: object = spec or {}
    for part in (dotted or "").split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    return str(cur or "").strip()
