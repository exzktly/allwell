"""Run-progress rendering for the Analyze pane.

The pipeline reports progress as a stream of events parsed out of its stdout by
:class:`services.pipeline_runner.ProgressTracker`; this module turns those
events into what the user sees — the dashboard headline, the overall bar, the
per-well plate map, and the ETA.

Held as a mixin rather than added to ``analyze/tab.py`` for the same reason
``AnalyzeSuspendMixin`` is: that file sits against its line budget, and the
rendering of run progress is a self-contained job with one entry point
(:meth:`AnalyzeProgressMixin._apply_progress_event`) rather than something the
tab's other behaviour reaches into.

The rule the whole module answers to: what it draws must never overstate what
the run achieved. A bar at 100% and a headline reading "N / N wells" are the
only signal the user has that a plate finished, so a count that can be reached
while wells are still outstanding is a lie the rest of the window cannot
correct. Hence the shortfall branches — a run that ends with fewer wells than
it announced says so in the headline and in the log, and a well announcing
completion twice is reported rather than folded into the count.
"""

from __future__ import annotations

import time as _time

from PySide6.QtCore import QTimer

from services import run_journal
from core.safe import swallow


def _fmt_secs(secs: float) -> str:
    """A duration for a label: ``42s``, ``7m03s``, ``2h15m``.

    Module-level because two live clocks render into this pane — the ETA
    counting down and the scheduler wait counting up — and two spellings of the
    same duration in the same window is how they come to disagree.
    """
    secs = int(round(max(0.0, secs)))
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs}s"


#: The phases a run passes through, named for what the headline says while the
#: run is in each. A run is in exactly one of them at a time.
_PHASE_PREPARE = "prepare"
_PHASE_GROUP = "group"
_PHASE_GROUPED = "grouped"
_PHASE_UPLOAD = "upload"
_PHASE_QUEUED = "queued"
_PHASE_LAUNCH = "launch"
_PHASE_PROCESS = "process"
_PHASE_FINALIZE = "finalize"

#: Which phase a coarse ``("stage", name)`` event puts the run into.
#:
#: ``download`` is deliberately absent. The fetcher pulls a finished well's
#: output while the wells after it are still computing, so a download event says
#: nothing about which phase the *run* is in — honouring it would flip the
#: headline between "Downloading" and "Processing" for the length of a plate.
#: What the downloads are doing is on the status line and in the Download rail
#: row; once the last well is computed, the finalize phase covers the drain.
_STAGE_PHASES = {
    "upload": _PHASE_UPLOAD,
    "process": _PHASE_PROCESS,
    "finalize": _PHASE_FINALIZE,
}


def _counted(verb: str, done: int, total: int, noun: str = "wells") -> str:
    """``"Grouping 3 / 40 wells  (7%)"`` — one spelling for every counted phase.

    A total of zero means the phase's population is not known yet rather than
    that it is empty, so the phrase counts up without a denominator instead of
    dividing by it — and says only what it is doing when nothing has been
    counted at all.
    """
    if total <= 0:
        return f"{verb} {noun}…" if done <= 0 else f"{verb} {done} {noun}…"
    return f"{verb} {done} / {total} {noun}  ({int(done / total * 100)}%)"


class AnalyzeProgressMixin:
    """Progress-event rendering for ``AnalyzeTab``.

    Expects the host to provide ``_dashboard``, ``_log_line``, ``_well_total``
    / ``_well_done`` / ``_well_repeats``, and ``_pipeline_started_at``.
    """

    #: How often the log repeats what the run is waiting for while a scheduler
    #: queues it. A queue wait can run to hours and is *normal*, so the status
    #: line carries the live clock and the log gets a breadcrumb at this
    #: interval — often enough that scrolling back explains the gap, rarely
    #: enough that it does not bury the run's real events.
    _SCHED_BREADCRUMB_S = 120.0

    #: The ``well_stage`` values that mean a well reached a terminal state, and
    #: what the run journal should record for each — for the events that state
    #: only a stage. A completion names its own record state as a third element
    #: and never comes through here, so ``"process"`` is deliberately absent:
    #: keeping it as an unreachable row made ``analysed`` appear *only* in this
    #: dict, and `tests/test_run_journal.py::test_every_well_state_is_written_by
    #: _some_caller` reads dict values — so the roster's liveness check passed
    #: on an entry nothing could reach (CLAUDE.md §7.5 #10).
    _JOURNAL_STAGES = {"error": "failed"}

    # ── the headline: one phase, one writer ─────────────────────────────────
    # The headline had several writers and none of them owned it. Each wrote a
    # string when its own phase started, and that string stayed on screen until
    # some *other* phase happened to write another — so every phase that wrote
    # nothing inherited whatever the previous one had said. A remote plate sat
    # on "Grouped — pipeline (segmentation + zipping)…" for the whole staging
    # step, the whole upload and the whole scheduler queue wait, describing work
    # that had already finished and naming the one thing that was not happening.
    #
    # So the headline is derived, never pushed: an event moves the run into a
    # phase and the phase decides the sentence. A phase that has nothing to say
    # leaves the label alone rather than blanking it, which is what keeps the
    # run's terminal states (Complete / Failed / Cancelled, written by the tab)
    # from being overwritten by a late progress event.

    def _reset_run_phase(self, *, remote: bool) -> None:
        """Put the run back in its pre-flight phase, for the run about to start.

        ``remote`` decides only what the phases *say* — a local run is starting
        a pipeline where a remote one is preparing an upload — so it is recorded
        here rather than re-derived per phase.
        """
        self._run_remote = bool(remote)
        self._run_phase = _PHASE_PREPARE
        self._run_ended = False
        self._render_headline()

    def _end_run_phase(self) -> None:
        """The run is over; its terminal state owns the headline from here.

        For the *success* headline, which the tab writes as plain text rather
        than through :meth:`~widgets.plate_progress.PlateProgress.set_done` —
        a run that finished short of the wells it announced says so there, and
        painting that green would be the shortfall dressed as a clean plate.
        Failures and cancels record an outcome the dashboard can be asked for,
        so they need no latch of their own.
        """
        self._run_ended = True

    def _set_run_phase(self, phase: str) -> None:
        """Move the run into *phase* and repaint the headline from it."""
        self._run_phase = phase
        self._render_headline()

    def _set_compute_phase(self) -> None:
        """Processing, or the finalize that follows the last well.

        The pipeline is still busy after the final well — the auto-threshold
        pass, and on a remote run the download drain and the remote cleanup —
        and a headline reading "Processing 40 / 40 wells  (100%)" throughout
        named the one thing the run had finished doing.
        """
        total, done = int(self._well_total), int(self._well_done)
        self._set_run_phase(_PHASE_FINALIZE if 0 < total <= done
                            else _PHASE_PROCESS)

    def _render_headline(self) -> None:
        """Write the current phase's sentence, if it has one and it changed.

        Nothing is written once the run has ended. A remote run keeps talking
        after it dies — ``verify_and_cleanup`` fetches what it can and logs it,
        so per-well events arrive after the failure has been painted — and a
        phase sentence written over the reason a run failed would replace the
        one thing the user needs with a description of a phase that is no
        longer running.

        What is written is compared against the label rather than against a
        remembered string: the tab writes the run's terminal states straight to
        the dashboard, so a cached copy here would go stale the moment a run
        ended and the comparison would then be against something nobody could
        see.
        """
        if getattr(self, "_run_ended", False) or self._dashboard.outcome():
            return
        text = self._phase_headline()
        if text and text != self._dashboard.headline_text():
            self._dashboard.set_headline(text)

    def _phase_headline(self) -> str:
        """The sentence for the phase the run is in; ``""`` if it has none."""
        phase = getattr(self, "_run_phase", _PHASE_PREPARE)
        if phase == _PHASE_PREPARE:
            return "Preparing…"
        if phase == _PHASE_GROUP:
            return _counted("Grouping", int(getattr(self, "_zipper_done", 0)),
                            self._dashboard.overall_maximum())
        if phase == _PHASE_GROUPED:
            # Staging rewrites every archive before the first byte is sent, and
            # on a large plate that is minutes — so the remote wording names the
            # upload it is preparing rather than a pipeline that will not start
            # until the scheduler says so.
            return ("Grouped — preparing the upload…"
                    if getattr(self, "_run_remote", False)
                    else "Grouped — starting the pipeline…")
        if phase == _PHASE_UPLOAD:
            done, total = self._dashboard.stage_progress("upload")
            return _counted("Uploading", done, total)
        if phase == _PHASE_QUEUED:
            # The wait's own clock and the job id are on the status line, which
            # ticks; saying the elapsed time twice in two places is how the two
            # come to disagree.
            return "Queued on the scheduler — waiting for a node…"
        if phase == _PHASE_LAUNCH:
            return "Starting the pipeline on the remote…"
        if phase == _PHASE_PROCESS:
            total, done = int(self._well_total), int(self._well_done)
            return _counted("Processing",
                            min(done, total) if total > 0 else done, total)
        if phase == _PHASE_FINALIZE:
            return f"Processed {int(self._well_done)} well(s) — finalizing…"
        return ""

    def _journal_well_stage(self, well: str, stage: str) -> None:
        """Record a terminal well stage in the output directory's run journal.

        For the stage-only events. ``ProgressTracker._count_well_done`` — the
        method that decides a well has finished — states the record's word in
        the event itself, so completions do not pass through this mapping.

        Hung off the same event the dashboard paints from, so what the record
        says and what the user saw cannot disagree — and written as it happens,
        which is the point: a local run that is killed has already recorded
        every well it finished, and nothing else on disk distinguishes those
        from wells it never started.

        Best-effort. A run whose journal cannot be written still runs, and its
        outputs remain the authoritative account of what it produced.
        """
        state = self._JOURNAL_STAGES.get(stage)
        if state is None:
            return
        self._journal_well(well, state)

    def _journal_well(self, well: str, state: str, *, reason: str = "") -> None:
        """Write one well's state into this run's journal, best-effort."""
        out_dir = getattr(self._runner, "last_output_dir", None)
        if out_dir is None:
            return
        # The token this run was opened with. It is what lets a completion line
        # still queued when the worker thread closed the record be accepted
        # anyway — these are parsed here, on the GUI thread, in bounded slices,
        # so the tail of every plate lands after the run has ended.
        token = getattr(self._runner, "journal_token", None)
        if token is None:
            return
        with swallow(f"journalling {well} as {state}"):
            run_journal.record_well(out_dir, well, state, run_token=token,
                                    reason=reason)

    def _on_scheduler(self, state: str, job_id: str, launcher: str) -> None:
        """A scheduler queued this run, or has just granted it a node.

        The reported symptom this answers: after "launching pipeline" the run
        went silent with the status still reading ``Transferring… 100%``, and
        nothing said it was queued. Two separate causes — the batched launcher
        announced nothing, and ``set_transfer(None)`` fires only on a *compute*
        event, which by definition has not happened yet while the job waits.
        """
        if state == "granted":
            self._stop_scheduler_timer()
            self._set_scheduler_status(
                f"Allocation granted{f' — job {job_id}' if job_id else ''}; "
                f"starting the pipeline…")
            self._set_run_phase(_PHASE_LAUNCH)
            return
        # The upload is over and the pipeline has not begun. Retract the
        # transfer label here rather than waiting for a compute event, which is
        # exactly what never arrives during a queue wait.
        self._dashboard.set_transfer(None)
        self._set_run_phase(_PHASE_QUEUED)
        if getattr(self, "_sched_since", None) is None:
            self._sched_since = _time.monotonic()
            self._sched_breadcrumb_at = self._sched_since
        # "queued" carries the job id, "waiting" the launcher; each arrives in
        # its own line, so keep whichever we have rather than overwriting one
        # with the other's blank.
        if job_id:
            self._sched_job = job_id
        if launcher:
            self._sched_launcher = launcher
        self._render_scheduler()
        timer = getattr(self, "_sched_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._render_scheduler)
            self._sched_timer = timer
        if not timer.isActive():
            timer.start()

    def _render_scheduler(self) -> None:
        """Write the live wait to the status line; breadcrumb it periodically."""
        since = getattr(self, "_sched_since", None)
        if since is None:
            return
        waited = _time.monotonic() - since
        job = getattr(self, "_sched_job", "")
        self._set_scheduler_status(
            f"Waiting for the scheduler — {_fmt_secs(waited)}"
            f"{f' (job {job})' if job else ''}")
        last = getattr(self, "_sched_breadcrumb_at", since)
        if waited > 0 and _time.monotonic() - last >= self._SCHED_BREADCRUMB_S:
            self._sched_breadcrumb_at = _time.monotonic()
            launcher = getattr(self, "_sched_launcher", "")
            # The request goes in the breadcrumb because an unsatisfiable one —
            # a partition that does not exist, a --gres the cluster cannot
            # honour, a --time past the queue's limit — is the usual reason a
            # job queues indefinitely, and seeing it beside the elapsed time is
            # what lets the user recognise that without leaving the app.
            self._log_line(
                f"[info] still waiting for the scheduler after "
                f"{_fmt_secs(waited)}"
                f"{f' (job {job})' if job else ''}"
                f"{f'; requested: {launcher}' if launcher else ''}\n",
                "INFO",
            )

    def _set_scheduler_status(self, text: str) -> None:
        """Write the status line and remember that this text is ours.

        The status line has several writers (the finalize phase, the plate-wide
        transfer). Recording ownership is what lets the scheduler message be
        retracted when the wait ends without stomping a line somebody else put
        there in the meantime.
        """
        self._sched_status_owned = True
        self._dashboard.set_status(text)

    def _clear_scheduler_status(self) -> None:
        """Drop the scheduler message once, if it is still the one on screen."""
        if getattr(self, "_sched_status_owned", False):
            self._sched_status_owned = False
            self._dashboard.set_status("")

    def _stop_scheduler_timer(self) -> None:
        timer = getattr(self, "_sched_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._sched_since = None
        self._sched_job = ""
        self._sched_launcher = ""

    def _update_eta(self, done: int, total: int) -> None:
        # Remember the latest progress snapshot so the tick timer can
        # re-render the ETA against live ``_time.monotonic()`` between
        # well_done events.
        self._eta_done = int(done)
        self._eta_total = int(total)
        # Lock in a deadline at this event using the cumulative per-well
        # wall-clock rate. The tick timer between events just renders
        # ``deadline - now`` — never recomputing from elapsed, which would
        # make the displayed remaining grow while waiting for the next
        # well to complete.
        started = getattr(self, "_pipeline_started_at", None)
        if (
            started is not None
            and self._eta_done > 0
            and self._eta_total > 0
            and self._eta_done < self._eta_total
        ):
            elapsed = _time.monotonic() - started
            if elapsed > 0.0:
                per_well_rate = elapsed / self._eta_done
                remaining_wells = self._eta_total - self._eta_done
                self._eta_deadline = _time.monotonic() + remaining_wells * per_well_rate
        self._render_eta()
        # Start the 1-Hz tick timer the first time we get usable progress
        # so the label counts down even when no new well_done arrives.
        timer = getattr(self, "_eta_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._render_eta)
            self._eta_timer = timer
        if done > 0 and total > 0 and done < total and not timer.isActive():
            timer.start()

    def _render_eta(self) -> None:
        started = getattr(self, "_pipeline_started_at", None)
        done = int(getattr(self, "_eta_done", 0))
        total = int(getattr(self, "_eta_total", 0))
        if started is None:
            self._dashboard.set_eta("")
            return
        if total > 0 and done >= total:
            # All wells finished. The pipeline still has post-well work
            # (write pipeline_info.json, run the auto-threshold pass)
            # before the "finished" event fires; surface that state
            # explicitly so the user doesn't see a stale ETA / blank
            # while the progress bar sits at 100%.
            self._dashboard.set_eta("Finalizing…")
            return
        deadline = getattr(self, "_eta_deadline", None)
        if deadline is None:
            # Pipeline is running but no well has completed yet — show a
            # placeholder so the user sees the timer is alive.
            self._dashboard.set_eta("ETA")
            return
        remaining = deadline - _time.monotonic()
        self._dashboard.set_eta(f"ETA {_fmt_secs(remaining)}")

    def _stop_eta_timer(self) -> None:
        timer = getattr(self, "_eta_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._eta_done = 0
        self._eta_total = 0
        self._eta_deadline = None
        # Called at the phase boundaries and at the end of a run. Nothing is
        # queued at any of them, and a scheduler clock still ticking after the
        # run finished would be the same false "still waiting" this change
        # exists to remove. A later hand-off restarts it.
        self._stop_scheduler_timer()

    def _apply_grouping_event(self, kind: str, payload: object) -> None:
        """Render the grouping phase's own events onto the dashboard.

        ``zipper_start`` opens the Group stage with the well count it is about
        to cover, ``zipper_well`` advances it one well, and ``zipper_done``
        closes it and hands the run to the pipeline. ``stage_total`` gives one
        rail row a denominator of its own, for a stage whose population differs
        from the run's (see
        :meth:`widgets.run_dashboard.RunDashboard.set_stage_total`) — on a
        remote run Group and Download span the whole plate while the stages
        between them only ever see the wells the upload split sent.

        All four live here rather than in the tab's polling loop because they
        write the headline, and every writer of that label is in this module.
        """
        if kind == "zipper_start":
            n = payload or 96
            self._zipper_done = 0
            self._dashboard.set_total(n)
            self._dashboard.set_overall_range(n)
            self._dashboard.set_overall(0)
            self._dashboard.set_active_stage("group")
            self._set_run_phase(_PHASE_GROUP)
        elif kind == "zipper_well":
            self._zipper_done = int(getattr(self, "_zipper_done", 0)) + 1
            self._dashboard.set_overall(self._zipper_done)
            self._update_eta(self._zipper_done,
                             self._dashboard.overall_maximum() or 96)
            self._dashboard.set_active_stage("group")
            if payload:
                self._dashboard.set_well_stage(str(payload), "group")
            self._set_run_phase(_PHASE_GROUP)
        elif kind == "zipper_done":
            self._dashboard.set_overall_range(100)
            self._dashboard.set_overall(0)
            self._well_total = 0
            self._well_done = 0
            self._set_run_phase(_PHASE_GROUPED)
            self._stop_eta_timer()
            self._dashboard.set_eta("ETA")
            # Reset the per-phase clock so the pipeline-phase ETA isn't biased
            # by the (typically short) grouping phase.
            self._pipeline_started_at = _time.monotonic()
        elif kind == "stage_total":
            stage, total = payload  # type: ignore[misc]
            self._dashboard.set_stage_total(str(stage), int(total))

    #: Events that only ever arrive once the pipeline is actually computing.
    #: Any one of them means a remote transfer, if there was one, is over — so
    #: the transfer label goes with it. Keyed off the pipeline's own progress
    #: rather than off rsync reaching exactly 100%, which it need not do: the
    #: last tick before rsync exits can be any value, and in batched mode it is
    #: scaled per batch. The label used to be left holding whatever that last
    #: number was for the whole compute phase.
    _COMPUTE_PHASE_EVENTS = ("well_total", "well_done", "well_fov")

    def _apply_progress_event(self, kind: str, payload: object) -> None:
        """Render a progress event from :class:`ProgressTracker` to the UI."""
        if kind in self._COMPUTE_PHASE_EVENTS:
            self._dashboard.set_transfer(None)
            # The pipeline is demonstrably running, so whatever it was queued
            # behind is over. A backstop for a host whose launcher announces
            # nothing on the way out of the queue: without it the clock would
            # keep counting a wait that had already ended, and the "allocation
            # granted" line would sit there while wells were computing.
            self._stop_scheduler_timer()
            self._clear_scheduler_status()
        if kind == "sched":
            state, job_id, launcher = payload  # type: ignore[misc]
            self._on_scheduler(str(state), str(job_id), str(launcher))
        elif kind == "well_total":
            # Announced once at the start of the run, and again if more wells
            # finish than were announced (the tracker treats its total as a
            # floor). Carry the running count across rather than resetting to
            # zero, so a re-announcement corrects the denominator without
            # rewinding the bar.
            total = int(payload)  # type: ignore[arg-type]
            self._well_total = total
            done = min(self._well_done, total)
            self._dashboard.set_total(total)
            self._dashboard.set_overall_range(total)
            self._dashboard.set_overall(done)
            self._set_compute_phase()
        elif kind == "well_done":
            done, total = payload  # type: ignore[misc]
            self._well_done = int(done)
            self._dashboard.set_overall(self._well_done)
            self._update_eta(self._well_done, total)
            self._set_compute_phase()
            # The auto-threshold pass runs *after* the last well — show
            # an explicit finalizing state so the user doesn't see the
            # progress bar pinned at 100% with nothing happening.
            if total > 0 and self._well_done >= total:
                self._dashboard.set_status("Finalizing…")
        elif kind == "well_repeat":
            # The same well announced completion twice. It is counted once, so
            # the headline stays honest; say what happened, because a repeat
            # means the well was processed (or skipped) a second time and that
            # is worth knowing — on a batched remote run it points at an input
            # zip that outlived its batch.
            self._well_repeats += 1
            self._log_line(
                f"[warn] {payload} reported completion more than once — "
                f"counted once. The well was processed or skipped again "
                f"(repeat {self._well_repeats} this run).\n",
                "WARNING",
            )
        elif kind == "stage":
            name = str(payload)
            self._dashboard.set_active_stage(name)
            phase = _STAGE_PHASES.get(name)
            if phase == _PHASE_PROCESS:
                self._set_compute_phase()
            elif phase is not None:
                self._set_run_phase(phase)
        elif kind == "well_stage":
            well, stage, *record = payload  # type: ignore[misc]
            self._dashboard.set_well_stage(str(well), str(stage))
            # A well reaching a stage is what moves that stage's count, and a
            # counting phase spells its count into the headline — so the
            # sentence is re-derived here rather than only when the *phase*
            # changes. Without it the upload phase froze at "Uploading 0 / 6"
            # for the whole upload: the phase was entered once, at the first
            # `("stage", "upload")` event, and every well that landed after it
            # moved the rail and nothing else.
            self._render_headline()
            if record:
                # The completion event carries the journal's word for what
                # happened, because the plate's word for it is deliberately
                # lossy: three different markers all paint "process". A run
                # that printed "up to date" (an incremental well with no new
                # FOVs) or "SKIP … output already exists" (a re-run without
                # --force) computed nothing, and recording those as
                # ``analysed`` claims work that did not happen — an
                # incremental run that touched 6 of 96 wells then read "It
                # recorded 96 finished". The journal has had a ``skipped``
                # state for exactly this since it was written; the local path
                # never wrote it.
                state = str(record[0])
                self._journal_well(
                    str(well), state,
                    reason="already up to date" if state == "skipped" else "")
            else:
                self._journal_well_stage(str(well), str(stage))
        elif kind == "well_fov":
            well, done, total = payload  # type: ignore[misc]
            self._dashboard.set_well_fov(str(well), int(done), int(total))
            # A field measured is a well computing, whatever the run announced
            # on its way here — the backstop for a host whose scheduler
            # hand-off went unannounced, matching the transfer/clock retraction
            # above.
            self._set_compute_phase()
        elif kind == "well_xfer":
            well, direction, frac = payload  # type: ignore[misc]
            self._dashboard.set_well_transfer(str(well), str(direction), float(frac))
        elif kind == "status":
            # Descriptive finalize-phase status (auto-threshold / remote drain
            # / cleanup) so the post-well work doesn't read as a hang.
            self._dashboard.set_status(str(payload))
