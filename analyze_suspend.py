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

Held as a mixin rather than added to ``analyze_tab.py`` for the same reason
``AnalyzeRunMixin`` is — that file sits against its line budget, and this is a
self-contained feature rather than something the tab's other behaviour reaches
into.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from dataclasses import dataclass

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
from well_viewer.core.safe import swallow, swallowed


@dataclass(frozen=True)
class _Resumable:
    """A run the banner can offer to continue, from either record.

    ``from_journal`` is not decoration: it decides which pointer is dropped
    when the user resumes or discards, and the two records are cleared by
    different calls.
    """

    spec: dict
    output_dir: str
    headline: str
    from_journal: bool


#: How each unfinished outcome is said, in the user's terms rather than the
#: journal's. Every one of them leaves the same thing behind — the wells that
#: got through — so what differs is only the account of why it stopped, and a
#: run that failed must not be described as one the app was closed during.
_UNFINISHED_PHRASES = {
    "error": "stopped on an error",
    "stopped": "was stopped",
    "suspended": "was suspended",
}


def _unfinished_headline(out_dir: str, journal) -> str:
    """One sentence naming the plate, when the run started, and how it ended."""
    when = time.strftime("%d %b %H:%M", time.localtime(journal.opened_at))
    if journal.is_open:
        ended = "never finished — All-Well closed while it was going"
    else:
        ended = _UNFINISHED_PHRASES.get(journal.outcome or "", "did not finish")
    return (f"A run on {plate_name(out_dir)} started {when} and {ended}. "
            f"Resuming continues it, computing only what is missing.")


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
        """A strip above the form offering the most recent suspended run.

        Hidden whenever there is nothing to resume, which is the normal state —
        it costs one row of chrome only when it has something to say.
        """
        banner = QWidget(parent)
        banner.setObjectName("ResumeBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self._resume_label = QLabel("", banner)
        self._resume_label.setWordWrap(True)
        row.addWidget(self._resume_label, 1)
        resume_btn = QPushButton("Resume", banner)
        resume_btn.setProperty("variant", "primary")
        resume_btn.clicked.connect(self._resume_suspended)
        row.addWidget(resume_btn)
        discard_btn = QPushButton("Discard", banner)
        discard_btn.clicked.connect(self._discard_suspended)
        row.addWidget(discard_btn)
        self._resume_banner = banner
        banner.setVisible(False)
        return banner

    def _hide_resume_banner(self) -> None:
        """Take the strip down without reading anything.

        The folder on the form has changed and nothing on screen describes the
        new one yet; leaving the old offer up until the read settles would show
        an offer for a plate the form no longer names.
        """
        banner = getattr(self, "_resume_banner", None)
        if banner is not None:
            banner.setVisible(False)

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
            return
        self._resume_label.setText(offer.headline + self._journal_detail(offer))
        banner.setVisible(True)

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

        Between the two records for the same folder the suspend record wins: it
        is the more specific statement, since the user asked for it.
        """
        if current is None:
            current = self._current_output_dir()
        # Filtering by folder *before* reading anything is the point: the index
        # holds an entry per plate ever run, and reading every one of their
        # journals to then discard all but one turned a keystroke into a
        # hundred-odd file opens. With a folder on the form there is exactly one
        # candidate, so read exactly one.
        offers = [*self._suspended_offers(only_dir=current),
                  *self._journal_offers(only_dir=current)]
        if current:
            # A folder is on the form and neither record describes it. Offering
            # some other plate here would be answering a question the user did
            # not ask; the pre-run report is what speaks for this folder.
            return offers[0] if offers else None
        return offers[0] if offers else None

    def _current_output_dir(self) -> str:
        """The output folder the form is pointing at, or ``""``.

        Read through the same planner the readout and the run both use, so
        "which folder is on screen" cannot come to mean two things.
        """
        raw = ""
        with swallow("reading the input field for the resume banner"):
            raw = self._input_edit.text().strip()
        if not raw:
            return ""
        with swallow("resolving the output folder for the resume banner"):
            from services.input_resolution_service import plan_input_layout

            plan = plan_input_layout(Path(raw))
            return str(plan.output_dir) if plan.output_dir is not None else ""
        return ""

    def _suspended_offers(self, *, only_dir: str = "") -> list[_Resumable]:
        """Suspended runs on record, newest first; one folder's when named."""
        return [
            _Resumable(
                spec=run.spec,
                output_dir=run.output_dir,
                headline=(
                    f"A suspended run is waiting: {run.describe()}. Resuming "
                    f"continues it — only the field/timepoint groups still "
                    f"missing are computed."),
                from_journal=False,
            )
            for run in load_all()
            if not only_dir or same_dir(run.output_dir, only_dir)
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
            offers.append(_Resumable(
                spec=journal.spec,
                output_dir=out_dir,
                headline=_unfinished_headline(out_dir, journal),
                from_journal=True,
            ))
        return offers

    def _journal_detail(self, offer: _Resumable) -> str:
        """The per-well line under the headline, or ``""`` when there is none.

        Reported rather than derived: these are the states the run itself
        recorded as it went. The counts deliberately do not claim anything
        about how complete the *output* is — that is
        ``pipeline.completeness``'s answer and the pane has no business
        offering a second one.
        """
        journal = run_journal.read_journal(offer.output_dir)
        if journal is None or not journal.wells:
            return ""
        parts = []
        for label, states in (
            ("finished", ("analysed", "fetched")),
            ("failed", ("failed",)),
            ("never fetched", ("not-fetched",)),
        ):
            n = len(journal.wells_in(*states))
            if n:
                parts.append(f"{n} {label}")
        return f" It recorded {', '.join(parts)}." if parts else ""

    def _forget_resumable(self, offer: _Resumable) -> None:
        """Stop offering *offer*, whichever record it came from.

        Neither branch deletes anything the run produced: the output folder is
        the work, and this drops only the pointer that keeps asking about it.
        """
        if offer.from_journal:
            run_journal.forget(offer.output_dir)
        else:
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

    def resume_aware_spec(self, spec: RunSpec) -> RunSpec:
        """Force incremental when this spec's input has a suspended run behind it.

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
        """
        try:
            raw = str((spec.opts or {}).get("raw") or "").strip()
            if not raw or spec.opts.get("incremental"):
                return spec
            if not any(same_dir(r.input_dir, raw) for r in load_all()):
                return spec
            opts = dict(spec.opts)
            opts["incremental"] = True
            self._log_line(
                "[resume] This input has a suspended run behind it — processing "
                "incrementally, so wells that were part-way through when it was "
                "suspended recover their missing field/timepoint groups instead "
                "of being skipped as complete.\n",
                "INFO",
            )
            return RunSpec(
                opts=opts,
                remote_name=spec.remote_name,
                cleanup_remote=spec.cleanup_remote,
                dry_run_cleanup=spec.dry_run_cleanup,
                output_filter=spec.output_filter,
                selective_channels=spec.selective_channels,
            )
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
            spec = RunSpec(
                opts=opts,
                remote_name=spec.remote_name,
                cleanup_remote=spec.cleanup_remote,
                dry_run_cleanup=spec.dry_run_cleanup,
                output_filter=spec.output_filter,
                selective_channels=spec.selective_channels,
            )
            error = self.start_run(spec)
            if error is not None:
                QMessageBox.critical(self, "Resume Error", error)
                return
            # Only once the run is actually going: a record cleared on a resume
            # that then failed to start would strand the half-finished output.
            self._forget_resumable(offer)
            self.refresh_resume_banner()
            self._log_line(
                f"\n[Resuming] {offer.headline} Incremental, so only the "
                f"field/timepoint groups missing from the output are computed.\n",
                "INFO",
            )
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
