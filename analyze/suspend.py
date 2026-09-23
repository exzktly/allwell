"""Suspend and resume for an Analyze run.

Stop kills a run; Suspend winds it down. The difference the user sees is what
survives: after a Stop, whichever wells were in flight are gone and the form
state goes with the window; after a Suspend, every well that had done any work
has written its CSV and its ``<well>_out.zip``, and the app remembers the spec
that produced them. Close All-Well, reopen it tomorrow, and the Analyze pane
offers to carry on.

The resume itself needs no new machinery. ``--incremental`` already reads a
half-finished output directory as its done-set of ``(fov, timepoint)`` groups
and recomputes only what is missing; :class:`services.run_spec.RunSpec` already
round-trips every form field, because the job queue needed exactly that. So this
module is the seam between them: a button that asks the run to wind down and
writes the spec down, and a banner that offers the spec back with incremental
forced on.

Held as a mixin rather than added to ``analyze/tab.py`` for the same reason
``AnalyzeRunMixin`` is — that file sits against its line budget, and this is a
self-contained feature rather than something the tab's other behaviour reaches
into.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dataclasses import dataclass

from ui import theme
from services import run_journal
from services.run_spec import RunSpec, RunSpecError
from services.suspend_record import (
    SuspendedRun,
    plate_name,
    same_dir,
    clear,
    load_all,
    save,
)
from core.safe import swallow, swallowed


@dataclass(frozen=True)
class _Resumable:
    """A run the banner can offer to continue, from either record.

    ``from_journal`` is not decoration: it decides which pointer is dropped
    when the user resumes or discards, and the two records are cleared by
    different calls.
    """

    spec: dict
    output_dir: str
    #: The whole offer as one sentence. Not what the banner renders — it is
    #: what the *log line* and the discard dialog say, both of which are prose
    #: in a stream of prose and have nowhere to put a layout. Composed from the
    #: three fields below rather than written beside them, so the strip and the
    #: log cannot come to describe the same run differently.
    headline: str
    from_journal: bool
    #: Two words naming the state the run is in — "Failed run", "Suspended
    #: run". The first thing read, and the only part that is the same length
    #: for every offer, so the strip has a fixed shape whatever it is about.
    kicker: str = ""
    #: Which plate, and when — the identity line. Answers "is this about the
    #: dataset I am looking at?", which is the question the user has before
    #: any other and the one an unscoped offer used to get wrong.
    title: str = ""
    #: What happened, and what Resume will do about it. Prose, and the only
    #: part that wraps to more than one line.
    detail: str = ""
    #: When the run stopped, as a unix timestamp. Only used to rank offers
    #: across the two records when no folder is on the form — each record is
    #: already newest-first within itself, but "the newest of both" cannot be
    #: had by concatenating them.
    at: float = 0.0


#: How each unfinished outcome is said, in the user's terms rather than the
#: journal's. Every one of them leaves the same thing behind — the wells that
#: got through — so what differs is only the account of why it stopped, and a
#: run that failed must not be described as one the app was closed during.
_UNFINISHED_PHRASES = {
    "error": "stopped on an error",
    "stopped": "was stopped",
    "suspended": "was suspended",
}


#: The two words the strip leads with for each outcome — the *state*, not the
#: sentence about it. Separate from the phrases above because they are read in
#: different places: the kicker is scanned, the phrase is read. A run the app
#: was killed during has no outcome recorded at all and is keyed on ``None``,
#: for the reason :data:`AnalyzeSuspendMixin._UNFINISHED_CAUSE` gives — reaching
#: it as a fallback would describe an outcome outside the roster as a crash,
#: which is a specific claim about a thing that did not happen.
_UNFINISHED_KICKERS = {
    "error": "Failed run",
    "stopped": "Stopped run",
    "suspended": "Suspended run",
    None: "Interrupted run",
}

#: What Resume does, said once. The strip, the one-sentence headline and the
#: log line all say it, and a user who reads one and then another must not be
#: told two things — which is what three separately-worded copies of this
#: sentence had already produced. The wording is the manual's.
_INCREMENTAL_NOTE = ("Resuming continues it, computing only the "
                     "field/timepoint groups still missing.")


def _unfinished_parts(out_dir: str, journal) -> tuple[str, str, str, str]:
    """``(kicker, title, detail, headline)`` for a run the journal calls unfinished.

    One function rather than four because every one of them is built from the
    same two facts — when it started and how it ended — and a strip whose
    kicker says "Stopped run" above a sentence saying it failed would be worse
    than either alone. The headline is composed here too, from the same locals,
    so the one-sentence rendering the log and the discard dialog use cannot
    drift from the three-part one on screen.
    """
    when = time.strftime("%d %b %H:%M", time.localtime(journal.opened_at))
    if journal.is_open:
        outcome = None
        ended = "never finished — All-Well closed while it was going"
    else:
        outcome = journal.outcome or ""
        ended = _UNFINISHED_PHRASES.get(outcome, "did not finish")
    plate = plate_name(out_dir)
    kicker = _UNFINISHED_KICKERS.get(outcome, "Unfinished run")
    return (
        kicker,
        f"{plate} · started {when}",
        f"It {ended}. {_INCREMENTAL_NOTE}",
        f"A run on {plate} started {when} and {ended}. {_INCREMENTAL_NOTE}",
    )


class AnalyzeSuspendMixin:
    """Suspend / resume behaviour for ``AnalyzeTab``.

    Expects the host to provide ``_runner``, ``_collect_run_spec``,
    ``_log_line``, ``_input_edit``, ``_well_done`` / ``_well_total``,
    ``start_run``, and ``_suspend_btn`` / ``_resume_banner`` widgets.
    """

    # ── suspending ───────────────────────────────────────────────────────
    def _suspend(self) -> None:
        """Record the run, then ask it to wind down.

        The record is written **first**. If the app is killed between the two
        steps the worst case is a stale offer to resume a run that never
        stopped, which the user can decline; the other order risks a run that
        winds down having left nothing behind to resume it with.

        First does not mean *conditional on*. The record is a pointer to the
        output folder, and the output folder is the work — so a record that
        cannot be written costs the resume banner and nothing else, and the
        run must still wind down. That is why the write has a scope of its own
        below rather than sharing this method's handler.

        Never lets an exception escape — PySide6 ≥ 6.5 aborts the process on an
        unhandled exception out of a Qt slot, and this one is reached from a
        button the user presses precisely when they want the work kept.
        """
        try:
            if not self._runner.is_running:
                return
            out_dir = self._runner.last_output_dir
            if out_dir is None:
                QMessageBox.warning(
                    self, "Suspend",
                    "This run has not resolved its output folder yet — there is "
                    "nothing to resume from. Use Stop to cancel it.")
                return
            # The record is best-effort; winding the run down is not. Anything
            # escaping the record write used to land in this method's own
            # handler, which returns — so the run carried on, and the button
            # the user pressed precisely to keep their work did nothing and
            # said nothing. Its own scope keeps the two independent.
            with swallow("writing the suspend record"):
                self._write_suspend_record(out_dir)
            self._runner.suspend()
            self._log_line(
                "\n[Suspending] Finishing the FOV-group(s) already in flight, "
                "then writing each well's CSV and out.zip. This can take a "
                "minute. Press Stop to give up on the in-flight work.\n",
                "WARNING",
            )
        except Exception:
            swallowed()  # a failed suspend must not take the window down

    def _on_run_suspended(self, payload, pending) -> None:
        """The run wound down after Suspend. Not a completion, and not a failure.

        A suspended pipeline exits 0, and before the runner reported that as
        its own event it arrived as ``done``: the dashboard read "Complete",
        the log printed "Processing Complete", and ``_on_run_done`` handed the
        half-written output folder to Review as a finished dataset. Review then
        loaded it from a timer callback and failed on the wells the run had
        never reached, which took the window down. So this path leaves
        ``_run_ok`` false — nothing downstream treats the plate as done, the
        suspend record survives ``_on_run_settled``, and the queue sees a
        suspend — and it never calls ``_on_pipeline_complete``.
        """
        self._log_lines(pending)
        self._end_run_phase()
        done, total = int(self._well_done or 0), int(self._well_total or 0)
        with swallow("putting the suspended run on the dashboard"):
            self._dashboard.set_cancelled("suspended; resume to finish")
            if total:
                self._dashboard.set_overall(done)
            self._dashboard.set_status("")
            self._dashboard.set_eta("")
        tally = f" {done} of {total} well(s) finished." if total else ""
        self._log_line(f"\n[Suspended]{tally} {payload}", "WARNING")

    def _write_suspend_record(self, out_dir: Path) -> None:
        """Persist the spec behind the running job, best-effort."""
        try:
            spec = self._collect_run_spec()
        except Exception:
            swallowed()  # a form that cannot be read cannot be resumed
            return
        run = SuspendedRun(
            spec=spec.to_json(),
            input_dir=self._input_edit.text().strip(),
            output_dir=str(out_dir),
            suspended_at=time.time(),
            # Both pointers carry it, because a suspend writes both. Filtering
            # the journal offer and not this one left the banner offering a
            # plate the queue still held — and `_resumable` puts the suspend
            # record *first* for the current folder, so the unfiltered one won.
            queued=bool(getattr(self._runner, "journal_queued", False)),
            wells_done=int(getattr(self, "_well_done", 0) or 0),
            wells_total=int(getattr(self, "_well_total", 0) or 0),
        )
        if not save(run):
            self._log_line(
                "[warn] Could not write the resume record; the output folder is "
                "still complete, so re-running this input with 'incremental' "
                "checked will pick up where it left off.\n",
                "WARNING",
            )

    def _build_suspend_button(self, parent: QWidget) -> QPushButton:
        """The Suspend button, built here rather than in the form monolith.

        It sits *before* Stop in the run-controls row because it is the one you
        want in the common case — the run is fine, you just need the machine
        back — and Stop is the escape hatch beside it. Disabled until a run is
        going, in step with Stop.
        """
        self._suspend_btn = QPushButton("⏸  Suspend", parent)
        self._suspend_btn.setEnabled(False)
        self._suspend_btn.setToolTip(
            "Wind the run down at the next field boundary and remember where it "
            "got to. Every well that has done any work keeps its results, and "
            "All-Well offers to carry on — after a restart if you like. Stop, by "
            "contrast, discards whatever is in flight.")
        self._suspend_btn.clicked.connect(self._suspend)
        return self._suspend_btn

    # ── the banner ───────────────────────────────────────────────────────
    def _build_resume_banner(self, parent: QWidget) -> QWidget:
        """A card above the form offering the most recent unfinished run.

        Hidden whenever there is nothing to resume, which is the normal state —
        it costs a block of chrome only when it has something to say.

        **Stacked, not a row, and deliberately not ``widgets.ContextBanner``.**
        That widget is this app's banner idiom and the first thing to reach for,
        but it is one ``QHBoxLayout``: glyph, sentence, confirm, cancel, all on
        one line. It fits where it is used because it is used on a *page* — the
        Job Queue borrows the wide right-hand area — and a sentence beside two
        buttons there still has most of the width. This strip lives in the
        Analyze sidebar, which is ``theme.Metrics.rail_w`` (329 px) and fixed,
        and whose scrolling viewport is 319 px of that. Measured in a real
        ``AnalyzeTab`` at the app's default window size, ``Resume`` and
        ``Discard`` beside the text leave the label **139 px**, so the offer
        reflows into a 192-px column of twelve lines running down the side of
        two buttons floating in the middle of it. Stacking the text above its
        own action row gives the same words **295 px** and four lines.

        Four labels rather than one paragraph, for the same reason: the strip
        has to answer four questions in a fixed order — what state the run is
        in, which plate and when, what happened and what Resume will do, and
        what the run recorded — and a single wrapping ``QLabel`` renders all
        four in one weight with no way to tell where one ends. The tally in
        particular is the *last* thing worth reading and was the hardest to
        skip past.
        """
        banner = QWidget(parent)
        banner.setObjectName("ResumeBanner")
        # A plain QWidget subclass ignores its own background rule without
        # this, so the card would paint nothing and read as loose page chrome —
        # which is what it did. ContextBanner carries the same line.
        banner.setAttribute(Qt.WA_StyledBackground, True)
        banner.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        col = QVBoxLayout(banner)
        col.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                               theme.Spacing.md, theme.Spacing.sm)
        col.setSpacing(theme.Spacing.xs)

        self._resume_kicker = QLabel("", banner)
        self._resume_kicker.setObjectName("AnalyzeBannerKicker")
        col.addWidget(self._resume_kicker)

        self._resume_title = QLabel("", banner)
        self._resume_title.setObjectName("AnalyzeBannerTitle")
        self._resume_title.setWordWrap(True)
        col.addWidget(self._resume_title)

        # Keeps the name the mixin and its tests have always used: this is the
        # label carrying the offer's prose.
        self._resume_label = QLabel("", banner)
        self._resume_label.setObjectName("AnalyzeBannerBody")
        self._resume_label.setWordWrap(True)
        col.addWidget(self._resume_label)

        self._resume_tally = QLabel("", banner)
        self._resume_tally.setObjectName("AnalyzeBannerTally")
        self._resume_tally.setWordWrap(True)
        col.addWidget(self._resume_tally)

        col.addWidget(self._build_resume_actions(banner))
        self._resume_banner = banner
        banner.setVisible(False)
        return banner

    def _build_resume_actions(self, banner: QWidget) -> QWidget:
        """The action row: Discard, then Resume, pushed to the right.

        Resume last because it is the one the strip exists for, and last is
        where the eye finishes a right-aligned group. Discard is not destructive
        — it drops two bookkeeping pointers and deletes nothing the run produced
        — but it is irreversible from the strip, so it keeps the default control
        look rather than being reduced to a link that can be hit on the way past,
        and the accent fill on Resume is what carries the ranking.
        """
        row = QWidget(banner)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, theme.Spacing.xs, 0, 0)
        lay.setSpacing(theme.Spacing.sm)
        lay.addStretch(1)

        discard_btn = QPushButton("Discard", row)
        discard_btn.setToolTip(
            "Stop offering this run. Nothing is deleted — every well that "
            "already finished keeps its CSV and its out.zip, and you can pick "
            "the plate up later by re-running the input with 'incremental' "
            "ticked.")
        discard_btn.clicked.connect(self._discard_suspended)
        lay.addWidget(discard_btn)

        resume_btn = QPushButton("Resume", row)
        resume_btn.setProperty("variant", "primary")
        resume_btn.setToolTip(_INCREMENTAL_NOTE)
        resume_btn.clicked.connect(self._resume_suspended)
        lay.addWidget(resume_btn)
        return row

    def _hide_resume_banner(self) -> None:
        """Take the strip down without reading anything.

        The folder on the form has changed and nothing on screen describes the
        new one yet; leaving the old offer up until the read settles would show
        an offer for a plate the form no longer names.
        """
        banner = getattr(self, "_resume_banner", None)
        if banner is not None:
            banner.setVisible(False)
        self._note_resume_card("")

    def _note_resume_card(self, output_dir: str) -> None:
        """Record which folder the card is describing, and say that it changed.

        The pre-run report sits directly beneath and is built from the same
        journal, so when this card is up for a folder the two state the same
        thing twice in a row. The report drops its own account of the run when
        that happens — which means it has to be told, and told by the one
        function that can change this card's visibility rather than by each of
        its callers. ``""`` means the card is down.

        The report is a sibling mixin on the same tab, and this is the reverse
        of the call ``settle_folder_banners`` already makes into this module.
        Guarded because the two are separable: a caller that mixes in the
        suspend behaviour without the pre-run report has no report to tell.
        """
        self._resume_shown_dir = str(output_dir or "")
        told = getattr(self, "resume_card_changed", None)
        if told is not None:
            told()

    def refresh_resume_banner(self, *, current_output_dir: str | None = None) -> None:
        """Show or hide the banner against what is on disk right now.

        Called at construction, once the input field settles, and after every
        run finishes — so a completed plate stops offering to resume itself and
        a freshly suspended one starts.

        ``current_output_dir`` is the folder the form names, already resolved.
        The caller usually has it, and re-deriving it here means walking the
        input volume a second time for one banner; ``None`` means "work it out",
        which is what the construction-time call needs.
        """
        banner = getattr(self, "_resume_banner", None)
        if banner is None:
            return
        offer = self._resumable(current_output_dir)
        if offer is None or getattr(self, "_running", False):
            banner.setVisible(False)
            self._note_resume_card("")
            return
        self._resume_kicker.setText(offer.kicker)
        self._resume_title.setText(offer.title)
        self._resume_label.setText(offer.detail)
        tally = self._journal_tally(offer)
        self._resume_tally.setText(tally)
        # An offer whose journal recorded nothing has no tally line, and an
        # empty QLabel still takes its line height plus the column's spacing —
        # a gap under the prose with nothing in it, which reads as a rendering
        # fault rather than as absence.
        self._resume_tally.setVisible(bool(tally))
        banner.setVisible(True)
        self._note_resume_card(offer.output_dir)

    # ── what there is to resume ──────────────────────────────────────────
    def _resumable(self, current: str | None = None) -> _Resumable | None:
        """The run this banner is offering, from either of the two records.

        A **suspended** run was wound down deliberately and is recorded by
        ``suspend_record``. An **unfinished** one — killed, failed or stopped —
        is recorded by the run journal, and until that existed left nothing at
        all behind: no banner, no offer, nothing naming the folder. The two are
        offered through one path because they need the same thing done to them,
        the recorded spec restarted with incremental forced on.

        **The folder on the form wins.** Both records are ordered newest-first
        across every plate, so taking the newest outright can offer a run
        against a dataset the user is not looking at — and with the pre-run
        report sitting directly below describing a *different* folder, the
        sidebar then contradicts itself and Resume acts on the plate that is
        not on screen. So a record for the folder currently in the form is
        preferred, and newest-anywhere is the fallback for a form with no
        folder in it yet, which is the state the app opens in and the one this
        banner was built for.

        Between the two records for the *same* folder the suspend record wins:
        it is the more specific statement, since the user asked for it. Across
        *different* folders it does not, and that is the distinction the first
        version of this missed — it concatenated the two lists and took the
        head, so every suspended record preceded every journal one however old.
        A plate suspended in February beat one the app was killed on this
        morning, in exactly the empty-form case both this docstring and the
        manual describe as "the most recent unfinished run on any plate".
        """
        if current is None:
            current = self._current_output_dir()
        if current == self.UNRESOLVED:
            # The form names an input this cannot resolve. That is not "no
            # folder named": falling through would rank every unfinished run
            # on record and offer the newest, described as though it were the
            # plate on screen, and accepting it would start an incremental run
            # against another dataset's output directory.
            return None
        # Filtering by folder *before* reading anything is the point: the index
        # holds an entry per plate ever run, and reading every one of their
        # journals to then discard all but one turned a keystroke into a
        # hundred-odd file opens. With a folder on the form there is exactly one
        # candidate, so read exactly one.
        suspended = self._suspended_offers(only_dir=current)
        from_journal = self._journal_offers(only_dir=current)
        if current:
            # One folder, so this is the specificity rule and nothing else: the
            # suspend record first, the journal behind it, and an empty list
            # when neither describes the folder — offering some other plate
            # here would answer a question the user did not ask, and the
            # pre-run report is what speaks for this folder.
            offers = [*suspended, *from_journal]
        else:
            # No folder named, so the question really is "what is the newest
            # unfinished run anywhere". Ranked by when each stopped, with the
            # suspend record breaking a tie for the reason above.
            offers = sorted([*suspended, *from_journal],
                            key=lambda o: (-o.at, o.from_journal))
        return offers[0] if offers else None

    #: `_current_output_dir`'s answer when the form names an input it cannot
    #: resolve. Distinct from `""`, which means "the form names nothing at
    #: all" — see `_current_output_dir` for why conflating them offered the
    #: user a different plate.
    UNRESOLVED = "\x00unresolved"

    def _current_output_dir(self) -> str:
        """The output folder the form is pointing at.

        Three answers, not two. ``""`` means the field is empty and the user
        has named nothing; :data:`UNRESOLVED` means they named something this
        cannot turn into an output folder — a path that does not exist, a
        volume that has gone away, a planner that raised; and anything else is
        the folder.

        Collapsing the middle case into ``""`` is what made the resume banner
        dangerous. ``_resumable`` reads ``""`` as "no folder named, so offer
        the newest unfinished run *anywhere*" — which is right for an empty
        form and wrong for a typo'd path, where it offered a resume for a
        completely different plate, described in the banner as though it were
        the one on screen. Accepting it would have started an incremental run
        against another dataset's output directory.

        Read through the same planner the readout and the run both use, so
        "which folder is on screen" cannot come to mean two things.
        """
        raw = ""
        with swallow("reading the input field for the resume banner"):
            raw = self._input_edit.text().strip()
        if not raw:
            return ""
        resolved = self.UNRESOLVED
        with swallow("resolving the output folder for the resume banner"):
            from services.input_resolution_service import plan_input_layout

            plan = plan_input_layout(Path(raw))
            if plan.output_dir is not None:
                resolved = str(plan.output_dir)
        return resolved

    def _suspended_offers(self, *, only_dir: str = "") -> list[_Resumable]:
        """Suspended runs on record, newest first; one folder's when named.

        A run the job queue started is skipped for the same reason
        :meth:`_journal_offers` skips one: the queue's own entry is already
        offering it, and resuming here while the queue still holds the job runs
        the same plate twice, concurrently, into one output directory. Both
        records have to be filtered, not one — a suspend writes both, and this
        is the one `_resumable` ranks first for the current folder.
        """
        return [
            _Resumable(
                spec=run.spec,
                output_dir=run.output_dir,
                headline=(f"A suspended run is waiting: {run.describe()}. "
                          f"{_INCREMENTAL_NOTE}"),
                from_journal=False,
                kicker="Suspended run",
                title=f"{run.plate()} · suspended {run.when()}",
                detail=f"It got {run.progress()}. {_INCREMENTAL_NOTE}",
                at=float(getattr(run, "suspended_at", 0.0) or 0.0),
            )
            for run in load_all()
            if not run.queued
            and (not only_dir or same_dir(run.output_dir, only_dir))
        ]

    def _journal_offers(self, *, only_dir: str = "") -> list[_Resumable]:
        """Unfinished runs the index knows of, newest first; one folder's when named.

        The folder filter is applied to the index entry, before the journal it
        names is opened. The index is one file; the journals are one directory
        read each, per plate ever run.
        """
        offers: list[_Resumable] = []
        for entry in run_journal.unfinished_runs():
            out_dir = str(entry.get("output_dir") or "")
            if only_dir and not (out_dir and same_dir(out_dir, only_dir)):
                continue
            journal = run_journal.read_journal(out_dir) if out_dir else None
            # An index entry outlives the folder it names — a deleted or moved
            # output directory must not produce a banner offering to resume it.
            if journal is None or not journal.spec or not journal.is_unfinished:
                continue
            if entry.get("dismissed"):
                # The user has closed this offer. That is a statement about the
                # banner; the entry stays unfinished so the incremental
                # recovery still fires on the next Run.
                continue
            if journal.queued:
                # The job queue started this run, so the queue's own entry is
                # already offering it. Offering it here too let a user resume
                # from the banner while the queue still held the job, and the
                # same plate then ran twice, concurrently, into one output
                # directory. Suspending from the queue is the case that reaches
                # this: the journal is closed ``suspended``, which is exactly
                # what ``is_unfinished`` is for.
                continue
            kicker, title, detail, headline = _unfinished_parts(out_dir, journal)
            offers.append(_Resumable(
                spec=journal.spec,
                output_dir=out_dir,
                headline=headline,
                from_journal=True,
                kicker=kicker,
                title=title,
                detail=detail,
                # When it stopped, not when it began: a run opened yesterday
                # and killed this morning is newer than one that began and
                # ended in between. An open record has no closing time, so it
                # falls back to when it was opened.
                at=float(journal.closed_at or journal.opened_at or 0.0),
            ))
        return offers

    def _journal_tally(self, offer: _Resumable) -> str:
        """The run's own per-well tally, as its own line, or ``""`` for none.

        ``RunRecord.tally`` returns a sentence written to be *concatenated* — it
        opens with a space, and with "It recorded", so it reads on from whatever
        precedes it. The other surface that renders it (the pre-run report in
        ``analyze.preflight``) still concatenates, so the shape stays as it is
        and the space is stripped here. Standing alone the sentence is still
        correct: the line above it has just named the run.
        """
        journal = run_journal.read_journal(offer.output_dir)
        return journal.tally().strip() if journal is not None else ""

    def _forget_resumable(self, offer: _Resumable) -> None:
        """Stop offering *offer* — from **both** records, not the one it came from.

        Neither call deletes anything the run produced: the output folder is
        the work, and these drop only the pointers that keep asking about it.

        There are two pointers and a plate can carry both. A run that was
        suspended writes a suspend record *and* leaves a journal entry, so
        dropping only the one the offer happened to be built from left the
        other behind: Discard cleared the banner, the next refresh read the
        surviving record, and the banner came straight back. The user's answer
        was recorded against one bookkeeping file and the question was being
        asked by two.

        `from_journal` still decides which record *sourced* the offer — that is
        what the banner's wording depends on — but it is not a claim that the
        other record does not exist.
        """
        with swallow(f"dropping the journal offer for {offer.output_dir}"):
            # ``dismiss``, not ``forget``. The index entry is what
            # ``unfinished_runs`` returns, and ``resume_aware_spec`` reads that
            # to decide whether the next Run must be incremental — so deleting
            # it here made Discard silently disarm the recovery, and the user
            # who dismissed the offer and pressed Run got the permanently
            # 3-of-12 plate the forcing exists to prevent.
            run_journal.dismiss(offer.output_dir)
        with swallow(f"dropping the suspend record for {offer.output_dir}"):
            clear(offer.output_dir)

    def _on_run_settled(self) -> None:
        """Called once a run reaches ``finished``, however it got there.

        A run that completed normally must stop offering to resume itself, so
        its record is dropped. A suspended or stopped one keeps its record: the
        stop case matters as much as the suspend case, because a hard Stop still
        leaves every finished well's CSV on disk and the user may well want to
        carry on from it.
        """
        try:
            out_dir = self._runner.last_output_dir
            completed = bool(getattr(self, "_run_ok", False)) and not (
                self._runner.was_stopped)
            if completed and out_dir is not None:
                clear(str(out_dir))
            self.refresh_resume_banner()
            # The run has just rewritten the folder the pre-run report
            # describes, so that report is now about the run before this
            # one. Re-read it here rather than waiting for the input field
            # to change, which after a run it never does.
            self.resync_preflight()
        except Exception:
            swallowed()

    #: How a previous run of the same input ended, in the words the resume
    #: message uses. The journal's `UNFINISHED_OUTCOMES` are three different
    #: events, and a line that says "suspended" about a run that *failed* is a
    #: statement the user can check and find untrue.
    _UNFINISHED_CAUSE = {
        "suspended": "a suspended run",
        "stopped": "a run that was stopped part-way",
        "error": "a run that failed part-way",
        None: "a run that never finished — the app was killed while it went",
    }

    def _input_has_unfinished_work(self, raw_input: str) -> str | None:
        """How *raw_input*'s last unfinished run ended, or ``None`` if none did.

        A phrase rather than a boolean, because the caller puts it in a sentence.
        Widening this from the suspend record to the journal widened the set of
        *causes* with it — an open record means the app was killed, and a closed
        one can say `error` or `stopped` as easily as `suspended` — and one
        message naming only the last of those is true a quarter of the time.

        The journal's index is keyed on the *output* directory, so the input it
        was run against is read from the journal in that directory rather than
        inferred: an output directory can be re-pointed between runs, and
        guessing one from the other is how a resume gets offered for a
        different plate.
        """
        from services import run_journal as _journal

        if any(same_dir(r.input_dir, raw_input) for r in load_all()):
            return self._UNFINISHED_CAUSE["suspended"]
        for entry in _journal.unfinished_runs():
            out = str(entry.get("output_dir") or "")
            if not out:
                continue
            with swallow(f"reading the run journal in {out}"):
                rec = _journal.read_journal(out)
                if rec is not None and rec.input_dir and same_dir(
                        rec.input_dir, raw_input):
                    # ``None`` is the *record left open* case, and it is
                    # keyed on the record being open rather than reached as a
                    # fallback: an outcome outside the roster — an index entry
                    # that still reads unfinished while the record on disk says
                    # something else — would otherwise be described to the user
                    # as "the app was killed while it went", which is a
                    # specific, checkable claim about a thing that did not
                    # happen. Whole point of the change was to stop saying one.
                    if not rec.outcome:
                        return self._UNFINISHED_CAUSE[None]
                    return self._UNFINISHED_CAUSE.get(
                        rec.outcome, "a run that did not finish")
        return None

    def resume_aware_spec(self, spec: RunSpec) -> RunSpec:
        """Force incremental when this spec's input has an unfinished run behind it.

        The Job Queue re-runs a job from its stored spec, and that spec has no
        ``incremental`` in it — the flag is not a form field, it is set by
        ``input_resolution_service`` or by a resume. So restarting a suspended
        queue job from the queue's own Start button would skip every well whose
        output zip exists and process the rest, which recovers the wells that
        never began but silently abandons the ones that were *in flight* when
        the run was suspended: those wrote a CSV and an out.zip from the
        FOV-groups they had finished, so they read as complete and are skipped,
        and their missing groups are never recovered. The plate comes back
        looking done.

        Matching is on the input directory, which is what the record stores and
        what ``opts["raw"]`` carries, and is exactly the identity the queue
        preserves when it re-runs a job.

        **Two records, not one.** ``suspend_record`` holds the runs the user
        wound down deliberately; the run journal holds every run that did not
        finish, including the ones nobody was there to record — an app that was
        killed, a machine that lost power, a crash. Asking only the suspend
        record meant the killed case, the one no artefact can report because a
        killed run and a run that simply processed fewer wells leave identical
        output, never got ``incremental`` and so left every half-finished well
        skipped as complete for ever. That is the defect this is for; the
        suspend case was only the half that already had a record.
        """
        try:
            raw = str((spec.opts or {}).get("raw") or "").strip()
            if not raw or spec.opts.get("incremental"):
                return spec
            cause = self._input_has_unfinished_work(raw)
            if cause is None:
                return spec
            opts = dict(spec.opts)
            opts["incremental"] = True
            self._log_line(
                f"[resume] This input has {cause} behind it — processing "
                "incrementally, so wells that were part-way through recover "
                "their missing field/timepoint groups instead of being skipped "
                "as complete. Tick Force to recompute the plate from scratch "
                "instead.\n",
                "INFO",
            )
            # `replace`, not a field-by-field rebuild: the rebuild dropped every
            # field it did not name, and `auto_detect` was one of them, so a
            # detection run resumed here ran the form's frozen layout instead.
            return dataclasses.replace(spec, opts=opts)
        except Exception:
            swallowed()  # never block a run because the record was unreadable
            return spec

    # ── resuming ─────────────────────────────────────────────────────────
    def _resume_suspended(self) -> None:
        """Restart the recorded spec with incremental forced on.

        Incremental is not a preference here, it is what makes the resume a
        resume: without it the pipeline skips any well whose CSV already exists
        and reprocesses nothing, so a plate that was half done would come back
        still half done, with the run reporting success.
        """
        try:
            offer = self._resumable()
            if offer is None:
                self.refresh_resume_banner()
                return
            spec = RunSpec.from_json(offer.spec)
            opts = dict(spec.opts)
            opts["incremental"] = True
            # Every other field as recorded — see `resume_aware_spec`.
            spec = dataclasses.replace(spec, opts=opts)
            error = self.start_run(spec)
            if error is not None:
                QMessageBox.critical(self, "Resume Error", error)
                return
            # Only once the run is actually going: a record cleared on a resume
            # that then failed to start would strand the half-finished output.
            self._forget_resumable(offer)
            self.refresh_resume_banner()
            # The headline already carries `_INCREMENTAL_NOTE`; the sentence
            # that used to follow it here said the same thing in different
            # words, which on a log line reads as two separate claims.
            self._log_line(f"\n[Resuming] {offer.headline}\n", "INFO")
        except RunSpecError as exc:
            QMessageBox.critical(self, "Resume Error", str(exc))
        except Exception:
            swallowed()

    def _discard_suspended(self) -> None:
        """Forget the record. The output folder itself is left alone."""
        try:
            offer = self._resumable()
            if offer is None:
                return
            choice = QMessageBox.question(
                self, "Discard unfinished run",
                f"Forget this unfinished run?\n\n{offer.headline}\n\n"
                f"Nothing is deleted — the results already written to\n"
                f"{offer.output_dir}\nstay exactly as they are. You can still "
                f"finish it later by re-running this input with 'incremental' "
                f"checked.",
            )
            if choice == QMessageBox.Yes:
                self._forget_resumable(offer)
                self.refresh_resume_banner()
        except Exception:
            swallowed()
