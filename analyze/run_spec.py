"""Analyze-pane run splitting, mirroring ``analyze/domain/input.py``'s precedent.

``AnalyzeTab`` mixes in :class:`AnalyzeRunMixin` for two members that split
"what would be run" from "actually run it": :meth:`_collect_run_spec` turns
the live form into a :class:`services.run_spec.RunSpec`, and :meth:`start_run`
launches that spec on this tab's runner. The split exists so a job queue can
persist a Analyze run's configuration as a plain, JSON-round-trippable
``RunSpec`` and start it later, without depending on the live form having the
same values it had at enqueue time.

The queue's *edit* flow needs the opposite direction too — a spec written back
into the form — which is :class:`analyze.domain.job_edit.AnalyzeJobEditMixin`, mixed
in here because it is part of the same queue-facing surface. ``show_job`` below
stays a shallow preview; ``hydrate_job`` is the full inverse.

``start_run`` never raises and never shows a dialog — it returns an error
string on failure so it stays callable headless (from a queue worker with no
form on screen); the caller decides how to surface the message.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QPushButton, QWidget

from analyze.domain.job_edit import AnalyzeJobEditMixin
from analyze.run_problems import AnalyzeRunProblemsMixin, RunProblem
from analyze.domain.well_list import incremental_is_moot, only_wells_note
from core.safe import swallowed
from services.pipeline_service import find_pipeline_script
from services.run_detect import (
    DetectionError,
    missing_input_error,
    resolve_detection,
)
from services.run_spec import RunSpec, RunSpecError
from widgets.flow_layout import FlowLayout


class AnalyzeRunMixin(AnalyzeJobEditMixin, AnalyzeRunProblemsMixin):
    """Splits run-collection (``_collect_run_spec``) from run-starting
    (``start_run``) so a :class:`RunSpec` can be persisted and started later,
    independent of the form state at collection time."""

    def _collect_run_spec(self) -> RunSpec:
        return RunSpec(
            opts=self._collect_run_options(),
            remote_name=self._remote_cb.currentData(),
            cleanup_remote="all" if self._cleanup_remote_cb.isChecked() else "none",
            dry_run_cleanup=self._dry_run_cleanup_cb.isChecked(),
            output_filter="full",
            selective_channels=True,
            auto_detect=self._auto_detect_on(),
        )

    def start_run(self, spec: RunSpec, *, pipeline: Path | None = None) -> str | None:
        """Start ``spec`` on this tab's runner. Returns an error message on
        failure (pipeline missing, remote no longer resolvable, a run already
        in flight), or ``None`` once the run has been launched.

        Every refusal is checked *before* any UI state is touched. This used to
        reset the progress tracker, dashboard, ETA and last-run card first and
        then let the runner's re-entrancy refusal escape as a `RuntimeError` —
        so a queue dispatch that raced a manual run wiped that run's readout on
        its way out, and `start_from_queue` never reached its `jobFinished`.
        """
        if pipeline is None:
            pipeline = find_pipeline_script()
        if pipeline is None:
            return "process_microscopy.py not found next to allwell.py."
        # Every entry point answers the same way. This was wired into
        # `start_from_queue` alone, so the Run button — the way almost everyone
        # restarts a plate — never asked, and a well that was mid-flight when
        # the previous run was killed stayed skipped as complete for ever.
        if self._runner.is_busy:
            return ("A pipeline is already running on the Analyze pane. Wait "
                    "for it to finish, or stop it, before starting another.")
        missing = missing_input_error(spec.to_json())
        if missing is not None:
            return missing
        orphan = _earlier_run_still_alive(spec)
        if orphan is not None:
            return orphan
        # Before `resume_aware_spec`, which reads the output folder the input
        # resolves to — and before any UI state is touched, like every other
        # refusal here. The scan reads a bounded sample of names (the same one
        # the form's Detect button reads on this thread), so it is not a walk.
        try:
            resolved, layout = resolve_detection(spec.to_json())
        except DetectionError as exc:
            return str(exc)
        if layout is not None:
            spec = RunSpec.from_json(resolved)
        self._detection_note = layout.summary() if layout is not None else ""
        spec = self.resume_aware_spec(spec)
        try:
            remote = spec.resolve_remote()
        except RunSpecError as exc:
            return str(exc)
        spec, resume_note = _nanowell_remote_resume_by_well(spec, remote)
        if resume_note:
            self._log_line(resume_note, "INFO")
        self._run_ok = False
        self._run_error = None
        self._set_running_ui_state(remote=remote)
        if self._detection_note:
            # In the log and on the dashboard, because the values detection
            # wrote are in no field on the form: a run under a detected layout
            # that said nothing about it would be indistinguishable from one
            # under the layout the form shows.
            self._log_line(self._detection_note, "INFO")
            self._dashboard.set_status(self._detection_note)
        scope = only_wells_note(spec.opts)
        if scope:
            # Same reason, and the status names both when both apply.
            self._log_line(scope, "INFO")
            self._dashboard.set_status(
                " — ".join(n for n in (self._detection_note, scope) if n))
        self._runner.start(
            pipeline,
            spec.runner_opts(),
            resolve_dirs=self._resolve_run_dirs_for_runner,
            remote=remote,
            output_filter=spec.output_filter,
            cleanup_remote=spec.cleanup_remote,
            dry_run_cleanup=spec.dry_run_cleanup,
            selective_channels=spec.selective_channels,
            # The run journal's resume payload. This is the one seam that has
            # the whole spec — the Run button, the resume banner and the job
            # queue all pass through here — while the output directory the
            # journal lives in is not resolved until inside the runner's own
            # thread. The same ``to_json`` the queue and the suspend record
            # already persist, so there is no second serialisation to keep in
            # step with the form.
            journal_spec=spec.to_json(),
            # Who owns offering this run back if it does not finish. A run the
            # queue started is already on the queue's own list, and the Analyze
            # banner offering it too let a user resume from the banner while
            # the queue still held the job — the same plate running twice,
            # concurrently, into one output directory.
            queued=bool(getattr(self, "_starting_from_queue", False)),
        )
        return None

    # ── job queue (QueueableController hooks) ───────────────────────────────
    def set_queue_sink(self, sink) -> None:
        """Where "+ Queue" sends a run. Injected by the Datasets pane, which
        owns the queue — so this module never imports it, exactly as the queue
        never imports ``analyze.tab``. Left unset when the pane is not in the
        build, and the button hides itself rather than failing on click."""
        self._queue_sink = sink
        btn = getattr(self, "_queue_btn", None)
        if btn is not None:
            btn.setVisible(sink is not None)

    def _build_run_row(self, parent: QWidget) -> QWidget:
        """The whole run-controls row: Run, Suspend, + Queue, Stop.

        Built here rather than in ``analyze/form.py`` because that file is
        at its line budget and the ratchet only moves down — moving the row out
        wholesale lowers it.

        The layout is a :class:`~widgets.flow_layout.FlowLayout`, not a
        ``QHBoxLayout``. The four buttons want 398px and the panel they sit in
        is ``theme.Metrics.rail_w`` (329) wide and fixed, so a box layout — which cannot wrap
        — would compress them until their labels were unreadable. FlowLayout
        also watches its children for show/hide, which matters here: "+ Queue"
        appears only once the Datasets pane injects a sink, and the row has to
        re-wrap when it does.
        """
        row = QWidget(parent)
        layout = FlowLayout(row, margin=0)
        layout.setContentsMargins(12, 10, 12, 10)

        self._run_btn = QPushButton("▶  Run Pipeline", row)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.clicked.connect(self._run)
        layout.addWidget(self._run_btn)

        layout.addWidget(self._build_suspend_button(row))
        layout.addWidget(self._build_queue_button(row))

        self._stop_btn = QPushButton("■  Stop", row)
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        layout.addWidget(self._stop_btn)
        self._show_run_buttons(running=False)
        return row

    def _show_run_buttons(self, *, running: bool) -> None:
        """Run while idle; Suspend and Stop while a run is going.

        Sets each button's enabled state, as the run's start and end always
        did, and its visibility: at idle the two run-only buttons used to sit
        beside Run disabled, and Stop's red label drew the eye before anything
        had started. Enabling comes first, so Stop turning enabled — the one
        signal the phone view's run-start watcher reads — still fires while
        the button is on its way onto the screen.
        """
        self._run_btn.setEnabled(not running and not self.has_pending_inputs())
        self._stop_btn.setEnabled(running)
        self._suspend_btn.setEnabled(running)
        self._run_btn.setVisible(not running)
        self._suspend_btn.setVisible(running)
        self._stop_btn.setVisible(running)

    def _build_queue_button(self, parent) -> QPushButton:
        self._queue_btn = QPushButton("+ Queue", parent)
        self._queue_btn.setToolTip(
            "Add this run to the Job Queue instead of running it now. It waits "
            "its turn in Datasets → Job Queue, which runs one job at a time.")
        self._queue_btn.clicked.connect(self._on_queue_requested)
        self._queue_btn.setVisible(getattr(self, "_queue_sink", None) is not None)
        return self._queue_btn

    def _on_queue_requested(self) -> None:
        """Queue the current form, through the same gate as Run.

        "+ Queue" used to hand the sink whatever ``collect_spec`` produced,
        skipping ``_validate_run_request`` entirely — so an unparseable schema,
        no fluorescent channels or an out-of-range numeric field was accepted
        without a word and sat in the queue looking identical to a valid job.
        It failed at dequeue time, in ``start_from_queue``, which reports
        through ``jobFinished`` because there is no dialog to show it to: the
        user came back to a queue entry marked failed and no statement of what
        was wrong with it.

        Validating here is not a duplicate of Run's check — it is the same
        check, with ``for_queue`` deferring only what a queued job cannot know
        yet: an input that is blank (so chaining can fill it) or not written
        yet, and with run-time detection on, the schema and channels. Those
        are asked again when the run starts, and the queue row says which are
        outstanding.
        """
        sink = getattr(self, "_queue_sink", None)
        if sink is None:
            return
        if not self.validate_for_queue():
            return              # already reported in a dialog naming the field
        sink(self.collect_spec())

    def validate_for_queue(self) -> bool:
        """Whether the form is a run the queue may accept, reporting why not.

        The one gate for every route into the queue: this page's "+ Queue",
        and the Job Queue's edit-session banner (``Add to queue`` / ``Save
        changes``), which the Datasets pane reaches through the injected
        collector registry. Two routes with two checks would disagree about
        what a runnable form is.
        """
        return self._validate_run_request(for_queue=True) is not None

    #: ``(path, note)`` while the input field holds a queued job's output that
    #: is not written yet, else ``None``. Read by ``_refresh_output``, which
    #: shows the note instead of planning a folder that does not exist, and
    #: dropped there as soon as the field holds anything else.
    _pending_input: tuple[str, str] | None = None

    def set_pending_inputs(self, pending: dict, projection: dict | None = None) -> None:
        """Show the input as the output of a queued job not yet run.

        *pending* maps ``"opts.raw"`` to the note shown under the input field;
        an empty dict clears it. Nothing reads the input while it is pending,
        so run-time detection is switched on — the schema and channels are read
        from the real files when the job runs, which is the only time there are
        any. When the job writing the input is expected to carry a known layout
        (*projection*'s ``layout``: a converter's naming and channel roles,
        passed through any Concatenate or Merge in between) the form is filled
        from it, the way a folder scan fills it, so the channel options can be
        set now; detection still checks them against the files.
        """
        note = (pending or {}).get("opts.raw", "")
        if not note:
            self._pending_input = None
            self._sync_run_for_pending()
            self._refresh_output()
            return
        self._pending_input = (self._input_edit.text().strip(), note)
        self._sync_run_for_pending()
        layout = (projection or {}).get("layout")
        if layout:
            self._apply_pipeline_info(dict(layout), set_input_path=False)
            settle = getattr(self, "_settle_labelfree_handover", None)
            if callable(settle):
                settle(hand_over=True)
        box = getattr(self, "_auto_detect_cb", None)
        if box is not None and not box.isChecked():
            box.setChecked(True)
        self._refresh_output()

    def has_pending_inputs(self) -> bool:
        return self._pending_input is not None

    def show_input_folder(self, path: str) -> None:
        """Put *path* in the input field, as choosing a folder does, without
        the folder scan: the folder a queued job will write is not there to
        scan (the Job Queue's "Output of a queued job ▸")."""
        if hasattr(self, "_input_edit"):
            self._input_edit.setText(str(path))

    def offer_queued_outputs(self, title, provider, choose, empty_text, tooltip) -> None:
        """A small menu button beside Browse offering *title* ▸ — the queued
        jobs ``provider()`` lists — as the Datasets pages offer it behind
        their Browse arrow. Added here rather than in the form builder so the
        Analyze form stays free of the queue, which injects it."""
        from PySide6.QtWidgets import QMenu, QToolButton

        from widgets.owned_action import owned_action

        browse = getattr(self, "_browse_btn", None)
        if browse is None or browse.parentWidget() is None:
            return
        button = QToolButton(browse.parentWidget())
        button.setObjectName("QueuedOutputButton")
        button.setText("▾")
        button.setToolTip(tooltip)
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        sub = QMenu(title, menu)
        menu.addMenu(sub)

        def fill() -> None:
            sub.clear()
            items = list(provider())
            if not items:
                owned_action(sub, empty_text).setEnabled(False)
                return
            for key, label in items:
                owned_action(sub, label).triggered.connect(
                    lambda _checked=False, k=key: choose(k))

        sub.aboutToShow.connect(fill)
        button.setMenu(menu)
        self._queued_output_menu = (button, menu, sub, fill)
        layout = browse.parentWidget().layout()
        layout.insertWidget(layout.indexOf(browse) + 1, button)

    def _sync_run_for_pending(self) -> None:
        """Grey Run, and say why, while the input is a queued job's output.

        As every Datasets page does (``datasets.core.pending_inputs``): a Run
        that stays lit and then refuses reads as a broken button, and the
        reason belongs on the button before it is pressed.
        """
        btn = getattr(self, "_run_btn", None)
        if btn is None:
            return
        pending = self.has_pending_inputs()
        btn.setEnabled(not getattr(self, "_running", False) and not pending)
        btn.setToolTip(
            "The input folder is the output of a queued job that hasn't run "
            "yet — add this to the queue to run it after that job."
            if pending else "")

    #: The whole form as it was before the Job Queue borrowed it
    #: (:meth:`remember_form`), put back by :meth:`restore_form` when a setup
    #: that changed it for its links ends — saved or cancelled — so the user's
    #: own form is not left with another job's input, channels and run-time
    #: detection.
    _form_before_borrow: dict | None = None

    def remember_form(self) -> None:
        """Keep the form as it is, before a queue session writes to it.

        Taken by the session before it populates the page, never inside
        :meth:`set_pending_inputs`: by then ``show_job`` or ``hydrate_job``
        has already replaced the input folder, and an edit's snapshot came
        back with an empty one.
        """
        self._form_before_borrow = self.collect_spec()

    def restore_form(self) -> None:
        before, self._form_before_borrow = self._form_before_borrow, None
        if before is not None:
            self.hydrate_job(before)

    def forget_form(self) -> None:
        self._form_before_borrow = None

    def _auto_detect_on(self) -> bool:
        box = getattr(self, "_auto_detect_cb", None)
        return bool(box is not None and box.isChecked())

    def _validate_run_request(self, *, for_queue: bool = False) -> Path | None:
        """Refuse a form that cannot run, saying every reason where Run is.

        Returns the pipeline script, or ``None`` after reporting why not —
        all the problems at once, under the Run button, with the first one's
        section unfolded and scrolled to (``analyze.run_problems``). It used to
        stop at the first problem and raise it in a modal dialog.

        *for_queue* is the "+ Queue" / queue-banner path, and it defers what a
        queued job cannot know yet. Its input may be a folder an earlier job
        has not written — or blank, for a job about to be chained after one —
        so nothing that reads the input is asked; ``start_run`` refuses a run
        that still has no input when it is dispatched, and the pipeline
        refuses a missing background or control well itself. With run-time
        detection on, the schema and channel checks are deferred on either
        path, because the values they would check are about to be replaced:
        ``services.run_detect`` re-asks them of the detected layout.
        """
        problems = self._run_problems(for_queue=for_queue)
        if not for_queue and self._pending_input is not None:
            # The folder is a queued job's output, not written yet. If an
            # earlier run left something there, running now would analyse
            # that instead — and say nothing about it.
            problems.append(RunProblem(
                "", "Input not written yet",
                "The input folder is the output of a queued job that hasn't "
                "run yet. Add this to the queue to run it after that job."))
        pipeline = find_pipeline_script()
        if pipeline is None:
            problems.append(RunProblem(
                "", "Configuration Error",
                "process_microscopy.py not found next to allwell.py."))
        self._report_run_problems(problems)
        return None if problems else pipeline

    def collect_spec(self) -> dict:
        """The current form's run, as a job queue's JSON spec."""
        return self._collect_run_spec().to_json()

    def show_job(self, spec: dict) -> None:
        """Populate the input field so switching to this tab shows what a
        queued job will run against, without starting it. Doesn't hydrate
        every form field — the queue re-derives a full RunSpec from the
        persisted JSON at dequeue time regardless, so the live form only
        needs to look right, not be authoritative."""
        raw = (spec.get("opts") or {}).get("raw")
        if raw and hasattr(self, "_input_edit"):
            self._input_edit.setText(str(raw))

    #: What run-time detection found for the run in flight, as one sentence,
    #: or ``""`` when the run did not ask for it. Logged when the run starts
    #: and handed to the queue as the finished job's message, so the row of a
    #: job whose schema and channels were never on any form says what they
    #: turned out to be.
    _detection_note = ""

    #: Set for the duration of :meth:`start_from_queue`, and read by
    #: :meth:`start_run` when it opens the journal. An attribute rather than a
    #: parameter because ``start_run`` is the seam every entry point shares and
    #: threading a flag through it would put the queue's vocabulary into the
    #: live form's call.
    _starting_from_queue = False

    def start_from_queue(self, spec: dict) -> None:
        """Build a RunSpec from the persisted JSON and launch it exactly as
        start_run() would from the live form. A remote that no longer
        resolves, or a missing pipeline script, reports failure through
        jobFinished rather than raising — there is no dialog to show it to."""
        # `start_run` applies `resume_aware_spec` itself now, for every caller.
        self._starting_from_queue = True
        try:
            error = self.start_run(RunSpec.from_json(spec))
        finally:
            self._starting_from_queue = False
        if error is not None:
            self.jobFinished.emit(False, error, "")

    def _stop(self) -> None:
        """The Stop button."""
        # Never let an exception escape this Qt slot (PySide6≥6.5 aborts the app).
        try:
            self._runner.stop()
            self._log_line("\n[User stopped the pipeline]\n", "WARNING")
            # Whatever the run was queued for, it no longer is: the scheduler
            # clock and its "still waiting" breadcrumbs end with the Stop, not
            # when the run thread finally reports back.
            self._stop_scheduler_timer()
            self._clear_scheduler_status()
        except Exception:
            swallowed()

    def stop(self) -> None:
        """QueueableController hook: cancel without waiting — callable from
        a queue bridge, which isn't a widget event."""
        self._runner.stop()

    def suspend(self) -> None:
        """QueueableController hook: wind down rather than cancel.

        Optional on the protocol — the queue probes for it and falls back to
        ``stop`` for the Datasets tools, which have no equivalent boundary to
        stop at. ``AnalyzeSuspendMixin._suspend`` is the button's path and also
        writes the resume record; this is the queue's, where the record is not
        needed because the queue is itself the record.
        """
        self._runner.suspend()

    def was_suspended(self) -> bool:
        """Whether the most recent run was wound down rather than cancelled.

        The queue reads this to tell a suspend apart from a Stop when the user
        clicked Suspend on the Analyze page rather than in the queue.
        """
        return self._runner.was_suspended

    def is_busy(self) -> bool:
        """QueueableController hook: whether a pipeline run is already in
        flight — checked by QueueBridge.show_job before it repopulates the
        input field from a clicked queued row, and by the queue's own dispatch
        gate before it starts the next job.

        Reads the runner's worker thread rather than the pipeline subprocess.
        The subprocess does not exist while a run is resolving directories and
        grouping input, so the narrower answer let the queue dispatch into a
        live run: `start_run` reset the running run's progress UI and then
        raised, leaving `_current` set with no `jobFinished` to come.
        """
        return self._runner.is_busy

    def was_cancelled(self) -> bool:
        """Whether the most recent run's failure was a user-requested Stop
        rather than a genuine failure — both surface as the same kind of
        ``error`` event, so a queue bridge needs this to tell a cancel apart
        from a failure when deciding whether to cascade-block successors."""
        return self._runner.was_stopped


def _earlier_run_still_alive(spec: RunSpec) -> str | None:
    """Why this plate cannot start yet: a pipeline from an earlier session is on it.

    A local pipeline outlives an app that crashed (it runs in a session of its
    own), and one that is still going when the restarted app resumes the same
    plate races it: the new run's grouping removes the ``in/<well>/`` folders
    the old one is reading, and whichever writes a well's CSV last wins. The
    old run winds itself down once it notices the app is gone
    (`pipeline.suspend.watch_parent`) and keeps a heartbeat in the output
    folder while it lives; this reads that heartbeat, for the Run button,
    Resume and the job queue alike, since all three start here.
    """
    from pipeline.suspend import live_pipeline_pid
    from services.input_resolution_service import plan_input_layout

    raw = str((spec.opts or {}).get("raw") or "").strip()
    if not raw:
        return None
    try:
        out_dir = plan_input_layout(Path(raw)).output_dir
    except Exception:
        swallowed("finding the output folder to check for a running pipeline")
        return None
    pid = live_pipeline_pid(out_dir) if out_dir is not None else None
    if pid is None:
        return None
    return (f"A pipeline from an earlier session (process {pid}) is still "
            f"writing to {out_dir}. It winds itself down once it notices the "
            "app that started it has closed, which takes about as long as one "
            "field; wait for it to finish, then try again. Starting now would "
            "race it for the same files.")


def _nanowell_remote_resume_by_well(spec, remote) -> tuple:
    """``(spec, note)``: a remote nanowell run the app made incremental, made a run by whole wells instead.

    ``resume_aware_spec`` (an unfinished run behind the input) and a resume after a suspend turn a run incremental,
    so that wells left part way recover their missing groups. A nanowell run cannot do that on a remote host — the
    host has none of a well's earlier timepoints, roster or template, and its tables would replace the local ones
    (``NANOWELL_REMOTE_INCREMENTAL_REFUSAL``) — and was refused, which left a stopped remote nanowell run with Force
    as its only way forward (roll-up review X29). It does not need to add to a well: the remote run skips every well
    finished here (``services.remote_finished_wells``, whose nanowell rule is that the tables cover every timepoint
    of the well's archive) and sends every other whole, to be analysed again in full and forced over any earlier
    output. So the incremental is dropped and the note says what will happen. ``force`` and a run on this computer
    are left as they are, and so is an Incremental the user ticked, which the form refuses before this. The key is
    read directly rather than through the nanowell field rules, which ship with the removable Nanowells feature.

    An Incremental that changes nothing is dropped too, ticked or not: under Force, or when every well the run
    touches is in *Force these wells* (``incremental_is_moot`` — QC triage's *Re-run wells* hands over exactly
    that), each well is rebuilt in full anyway, so the run is the same without it — and with it the remote runner
    refuses the run (``refuse_remote_nanowell_incremental``). The form lets that case through for this reason.
    """
    import dataclasses

    opts = spec.opts or {}
    nanowell = bool(str(opts.get("nanowell_format") or "").strip())
    if remote is None or not nanowell or not opts.get("incremental"):
        return spec, ""
    if incremental_is_moot(opts):
        note = ("[nanowell] Incremental is off for this remote run: every well it runs is forced, so each is "
                "analysed again in full either way, and a remote host cannot add to a nanowell well part-way.\n")
        return dataclasses.replace(spec, opts={**opts, "incremental": False}), note
    note = ("[resume] A nanowell run cannot add to a well part-way on a remote host, so this one goes well by well: "
            "every well whose results here cover all its timepoints under these settings is skipped, and every "
            "other well — left part way, or never finished — is sent whole and analysed again in full.\n")
    return dataclasses.replace(spec, opts={**opts, "incremental": False}), note
