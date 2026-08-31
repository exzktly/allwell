"""What the Analyze pane can say about a run *before* it starts.

The output directory is known the moment an input folder is chosen —
``input_resolution_service.plan_input_layout`` derives it and reads nothing
else — so everything an earlier run left there is available while the form is
still being filled in: the run journal, the ``pipeline_info.json`` sidecar that
run wrote, and the outputs themselves. None of it was shown. A user pointing
All-Well at a folder they last touched a week ago got a blank form and no
statement of what was already done, and found out what the run would do by
running it.

This module is the answer to that. It reports in two tiers, split by what each
costs:

**Tier one is cheap enough to run inline** — the journal is one small JSON
read, and the scope line adds one ``iterdir`` of the input directory plus, when
loose images are sitting in it, a central-directory read of every well archive
they belong to — that is how the duplicate rule knows what each archive already
holds. A background or flat-field method set to ``empty_well`` adds a second
pass over the same archives. Not free, and not a directory *walk*: bounded by the plate's own
top level, which is why it can hang off the existing ``_refresh_output`` once
the field has settled. It says what the last run against this folder did, and
above all whether it *finished*: a journal still open is a run the app was
killed during, which nothing else on disk records.

**Tier two costs a directory walk**, so it runs on a worker and arrives when it
arrives. ``pipeline.completeness.scan_run`` is the same predicate the
end-of-run check and Datasets → Validate use, which is the point — a preview
computing its own idea of coverage is a preview that can contradict the run it
is describing.

Two rules the module is built around.

**It offers; it does not apply.** The settings that produced the existing
output are restored only when the user asks, through the same
``_apply_pipeline_info`` the manual *Open pipeline_info.json* path uses. A form
the user has been typing into is not overwritten because they pointed at a
folder. And the run-scope knobs — ``force``, ``force_wells``, ``incremental``
— are never restored at all: they say what the *last* run was told to do, and
silently re-forcing a plate because the previous run was forced would be the
most expensive kind of surprise.

**It reports; it does not decide.** Every *claim* comes back verbatim from the
function that owns it — ``ScopePlan.preview`` for what the run will do about new
images, ``completeness``'s own counts for coverage, the journal's recorded
states for what happened — and this module supplies only the connective prose
between them, so the preview and the run cannot disagree about a value. That is
deliberately weaker than ``tests/test_layout_readout_single_source.py``'s rule
over the layout line beside it, which requires the label to *be* a quoted
planner value; that shape suits a one-sentence readout, not a paragraph drawn
from four sources. The piece that could drift is the scope line, and
``tests/test_analyze_preflight.py::test_the_scope_line_is_the_planners_own_sentence``
pins it to the planner's own words.

Held as a mixin rather than folded into ``analyze/tab.py`` for the reason
``analyze.suspend`` and ``analyze.progress`` were — that file sits against its
line budget, and this is a self-contained feature with one entry point.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

import theme
from services import run_journal
from services.input_resolution_service import plan_incremental_scope
from pipeline import completeness
from well_viewer.core.safe import swallow, swallowed

#: How long the input field must stay still before anything touches the disk.
#: ``textChanged`` fires on every keystroke, so a path typed rather than picked
#: would otherwise start one scan per character — each of which opens every
#: archive in the plate.
_SETTLE_MS = 400

#: Run-scope knobs are deliberately not restored from a previous run. See the
#: module docstring: they record what that run was *told* to do.
_SCOPE_KEYS = ("force", "force_wells", "incremental")


class _ScanSignals(QObject):
    """The worker's way back onto the GUI thread.

    A queued signal, not ``QTimer.singleShot`` — a worker thread has no event
    loop, so a timer armed there never fires and the result would be dropped in
    silence.
    """

    done = Signal(str, object)          # output_dir, run_check block or None


class AnalyzePreflightMixin:
    """Pre-run reporting for ``AnalyzeTab``.

    Expects the host to provide ``_input_edit``, ``_apply_pipeline_info``,
    ``_collect_run_options``, ``_log_line`` and ``_running``.
    """

    # ── construction ─────────────────────────────────────────────────────
    def _build_banner_strips(self, parent: QWidget) -> None:
        """Add the strips that sit above the form, in the order they read.

        Both answer "is there unfinished work here?", from the two records that
        can know — the suspend/interrupt record, then this folder's own run
        journal — so they are built together rather than from two lines in
        ``analyze.form``, which is against its line budget and whose every
        addition has to buy its place.

        They share a holder purely so there is a gap *between* them when both
        are up: the form's own layout runs at zero spacing, and two bordered
        cards stacked flush read as one box with a line through it. The holder
        takes no margins of its own — the cards run the full width of the
        sidebar, which is what puts their text on the same 12-px left edge as
        every section body beneath — and a holder whose children are both
        hidden, which is the normal state, contributes no height at all.
        """
        holder = QWidget(parent)
        strips = QVBoxLayout(holder)
        strips.setContentsMargins(0, 0, 0, 0)
        strips.setSpacing(theme.Spacing.sm)
        strips.addWidget(self._build_resume_banner(holder))
        strips.addWidget(self._build_preflight_banner(holder))
        parent.layout().addWidget(holder)

    def _build_preflight_banner(self, parent: QWidget) -> QWidget:
        """The card that reports what is already in the output folder.

        Hidden until there is something to report, which is the normal state
        for a fresh plate — it costs a block of chrome only when it has
        something to say.

        Built to the same shape as the resume strip directly above it (see
        ``analyze.suspend._build_resume_banner`` for why that shape is stacked
        rather than a row): text first, its own right-aligned action row
        beneath. The reason is sharper here, because this one's buttons are the
        wider pair: *Load these settings* and *Dismiss* side by side left the
        report **74 px** of the sidebar's 319-px viewport to wrap into — 224 px
        of text two words wide — and the report runs to three sentences once
        the coverage scan and the scope line have landed. Stacked, the same
        words get 295 px.

        It is the quieter of the two by design. The resume strip carries an
        action the user can take right now and is bordered in ``accent``; this
        one states what is already true of the folder and is bordered in
        ``border``. Their fills are the same, so they read as one family of
        report rather than as two unrelated pieces of chrome.
        """
        banner = QWidget(parent)
        banner.setObjectName("PreflightBanner")
        # Without this a plain QWidget subclass ignores its own background
        # rule — see widgets/context_banner.py, which carries the same line.
        banner.setAttribute(Qt.WA_StyledBackground, True)
        banner.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        col = QVBoxLayout(banner)
        col.setContentsMargins(theme.Spacing.md, theme.Spacing.sm,
                               theme.Spacing.md, theme.Spacing.sm)
        col.setSpacing(theme.Spacing.xs)
        self._preflight_label = QLabel("", banner)
        self._preflight_label.setObjectName("AnalyzeBannerBody")
        self._preflight_label.setWordWrap(True)
        col.addWidget(self._preflight_label)

        row = QWidget(banner)
        actions = QHBoxLayout(row)
        actions.setContentsMargins(0, theme.Spacing.xs, 0, 0)
        actions.setSpacing(theme.Spacing.sm)
        actions.addStretch(1)
        dismiss = QPushButton("Dismiss", row)
        dismiss.setToolTip(
            "Close this report for the folder in the form. It comes back if "
            "you point somewhere else, or once a run has written here again.")
        dismiss.clicked.connect(self._dismiss_preflight)
        actions.addWidget(dismiss)
        self._preflight_load_btn = QPushButton("Load these settings", row)
        self._preflight_load_btn.setToolTip(
            "Fill the form with the settings that produced the output already "
            "in this folder. Reprocessing options are left as you have them.")
        self._preflight_load_btn.clicked.connect(self._load_previous_settings)
        actions.addWidget(self._preflight_load_btn)
        col.addWidget(row)
        self._preflight_banner = banner
        banner.setVisible(False)

        # Parented to the banner so its C++ lifetime is the widget's. A scan
        # thread outlives the widget whenever the pane is closed mid-scan, and
        # an unparented signals object is collected the moment Python drops it
        # — leaving the thread to emit into a deleted C++ object.
        self._preflight_signals = _ScanSignals(banner)
        self._preflight_signals.done.connect(self._on_coverage_scanned)
        self._preflight_timer = QTimer(banner)
        self._preflight_timer.setSingleShot(True)
        self._preflight_timer.setInterval(_SETTLE_MS)
        self._preflight_timer.timeout.connect(self.settle_folder_banners)
        #: Output dir the visible report describes; guards a scan that lands
        #: after the user has moved on to a different folder.
        self._preflight_dir: str = ""
        self._preflight_dismissed: str = ""
        self._preflight_coverage: str = ""
        #: The folder a scan is currently walking, or ``""`` for none. One
        #: scan at a time: the settle timer bounds how often a scan is *asked*
        #: for, not how long one takes, and a plate on a slow network volume
        #: outlives several settles — which would put a thread per settle onto
        #: the same directory, all but the last of whose answers is discarded
        #: on arrival anyway. A folder chosen while one is in flight is held
        #: here and started when it lands.
        self._preflight_scanning: str = ""
        self._preflight_scan_queued: bool = False
        return banner

    # ── tier one: the journal, immediately ───────────────────────────────
    def _refresh_folder_banners(self, plan, *, immediate: bool = False) -> None:
        """Note the folder the form now names; read it once the field settles.

        Both strips answer one question between them — what does this folder
        already hold — so they move together, and **neither touches the disk
        from here**. This runs on `textChanged`, which fires once per keystroke,
        and every read behind these banners is per-folder: the journal, the
        resume index, the loose-file plan. Doing them inline cost 9.6 ms and 140
        file opens for a single character typed into the path field, on a
        machine with a dozen plates on record.

        So this does only what must be immediate — remember the target, and take
        a stale banner down — and starts the settle timer. Picking a folder with
        Browse fires `textChanged` once and the delay is imperceptible; typing a
        path fires it thirty times and now costs one read at the end instead of
        thirty times the whole set.
        """
        banner = getattr(self, "_preflight_banner", None)
        if banner is None:
            return
        output_dir = getattr(plan, "output_dir", None) if plan is not None else None
        self._preflight_input = str(getattr(plan, "input_dir", "") or "")
        target = str(output_dir) if output_dir is not None else ""
        if target != self._preflight_dir:
            # A different folder: nothing either banner is showing applies to
            # it, and leaving them up until the timer fires would have the
            # sidebar describing a plate the form no longer names. The stored
            # headline goes with the coverage line — it is the previous
            # folder's sentence, and it is what a repaint would put back up.
            self._preflight_coverage = ""
            self._preflight_headline_text = ""
            banner.setVisible(False)
            self._hide_resume_banner()
        self._preflight_dir = target
        self._preflight_timer.stop()
        if immediate:
            # Construction and end-of-run are single events, not a keystroke
            # storm: there is nothing more coming, and making the user wait out
            # a settle delay to be told what is already on disk is delay for
            # nothing.
            self.settle_folder_banners()
        else:
            self._preflight_timer.start()

    def settle_folder_banners(self) -> None:
        """Read the folder and paint both strips. The timer's only job.

        Public because it is also the seam a test drives: waiting out a real
        400 ms in every test that types a path is dead time, and calling this is
        the same code the timer calls rather than a way around it.
        """
        banner = getattr(self, "_preflight_banner", None)
        if banner is None:
            return
        target = self._preflight_dir
        # The resume offer is scoped to this same folder, and it is handed the
        # already-resolved path rather than re-deriving it: `plan_input_layout`
        # walks the input volume, and doing that twice per settle is one walk
        # too many.
        self.refresh_resume_banner(current_output_dir=target)
        if not target or getattr(self, "_running", False):
            banner.setVisible(False)
            return
        if self._preflight_dismissed and _same(self._preflight_dismissed, target):
            banner.setVisible(False)
            return
        headline = self._preflight_headline(Path(target))
        if not headline:
            banner.setVisible(False)
            return
        self._render_preflight(headline)
        self._start_coverage_scan()

    def suspend_preflight(self) -> None:
        """Hide the report for the duration of a run.

        What it says is a statement about the folder as it stood *before* this
        run, and the run is in the middle of making it untrue. Leaving it up
        would have the sidebar describing the previous run while this one is
        overwriting exactly what it describes.
        """
        banner = getattr(self, "_preflight_banner", None)
        if banner is not None:
            banner.setVisible(False)

    def resync_preflight(self) -> None:
        """Re-read the output folder once a run has settled, however it ended.

        Without this the report is only ever computed from ``textChanged``, so
        after a run the sidebar still describes the run *before* it — which on
        a failure is the one case where the user most needs to be told what
        just happened, and is told the opposite.

        Two pieces of state have to go first. The coverage line was measured
        against the pre-run output and the folder has not changed, so nothing
        else would clear it. And a dismissal is scoped to a folder as it stood
        when it was dismissed; a run that has since written to that folder is
        new information, not the thing the user waved away.
        """
        self._preflight_coverage = ""
        self._preflight_dismissed = ""
        with swallow("re-reading the output folder after a run"):
            self._refresh_output(immediate=True)

    def _preflight_headline(self, out_dir: Path) -> str:
        """What the last run against *out_dir* did, or ``""`` if none did.

        The three cases are genuinely different and are said differently. A run
        still recorded as open is one the app was killed during — the only one
        of the three that no artefact can report, and the one worth leading
        with. A closed record names how it ended. And a folder with outputs but
        no journal at all is every dataset produced before the journal existed,
        which must still be reported or the feature would be useless on exactly
        the plates a user already has.
        """
        journal = run_journal.read_journal(out_dir)
        if journal is None:
            return ("This folder already holds output from an earlier run. "
                    "Its settings can be loaded from the sidecar it wrote."
                    if (out_dir / "pipeline_info.json").is_file() else "")
        when = time.strftime("%d %b %H:%M", time.localtime(journal.opened_at))
        if journal.is_open:
            head = (f"A run started {when} never finished — All-Well closed "
                    f"while it was going.")
        else:
            ended = {
                "ok": "completed",
                "error": "failed",
                "stopped": "was stopped",
                "suspended": "was suspended",
            }.get(journal.outcome or "", "ended")
            head = f"The last run here started {when} and {ended}."
        return head + journal.tally()

    def _preflight_is_wanted(self) -> bool:
        """Whether the report may be on screen at all, right now.

        Two states forbid it, and both are answers the user has already given:
        a run is going, so the report describes a folder this run is in the
        middle of making untrue (:meth:`suspend_preflight`); or they closed the
        strip for this folder, and it must stay closed until they point
        somewhere else.
        """
        if getattr(self, "_running", False):
            return False
        target = self._preflight_dir
        dismissed = getattr(self, "_preflight_dismissed", "")
        return not (target and dismissed and _same(dismissed, target))

    def _resume_card_covers(self, target: str) -> bool:
        """Whether the resume card is up, describing this same folder.

        When it is, it has already said what the last run did and what it
        recorded — from the same journal this report reads — so the report
        saying it again puts the identical sentence in two bordered cards
        stacked one above the other.

        Keyed on the folder, not on "is the card visible": with no folder on
        the form the card falls back to the newest unfinished run on any plate,
        and that offer says nothing about the folder this report describes.
        """
        shown = getattr(self, "_resume_shown_dir", "")
        return bool(shown and target and _same(shown, target))

    def resume_card_changed(self) -> None:
        """Repaint the report because the card above appeared or went away.

        Called by ``analyze.suspend._note_resume_card``. What the report says
        depends on whether that card is covering the same folder, so the
        decision is made at paint time and every path that changes the card —
        a folder settling, Discard, Resume, a run starting or ending — comes
        back through here rather than each having to remember.

        Two early returns, and both are about cost rather than tidiness.
        Repainting runs :meth:`_scope_line`, which reads the input directory
        and the central directory of every archive a loose image belongs to,
        and this is reached once per settle — so it repaints only when the
        answer it depends on has actually flipped, and never before the folder
        has been read at all, which is the settle's own job and is about to
        happen anyway.
        """
        if not getattr(self, "_preflight_headline_text", ""):
            return
        covers = self._resume_card_covers(self._preflight_dir)
        if covers == getattr(self, "_preflight_suppressed", None):
            return
        self._render_preflight(self._preflight_headline_text)

    def _render_preflight(self, headline: str) -> None:
        """Paint the banner, coverage line included when one has arrived.

        The visibility gates are asked here rather than by each caller because
        this is the only place the strip is shown, and the callers are not all
        immediate: a coverage scan walks the output volume on a worker thread
        and lands whenever it lands. ``settle_folder_banners`` checked both
        gates and this did not, so a scan of a slow volume that started before
        Run was pressed came back five seconds into the run and put the strip
        up describing the folder the running job was overwriting — the exact
        thing ``suspend_preflight``'s docstring says must not happen — and a
        strip the user had dismissed came back the same way, with no second
        dismissal available because the banner had never been re-offered.

        The text is still updated when the strip is not wanted: it is what the
        next legitimate paint will show, and computing it twice is the only
        alternative.
        """
        banner = getattr(self, "_preflight_banner", None)
        if banner is None:
            return
        # Stored unsuppressed. The suppression below is a fact about what is
        # on screen *now*, and the card above can come down — a Discard, a
        # Resume — with this folder unchanged, at which point the report is the
        # only thing left that can say what the last run did.
        self._preflight_headline_text = headline
        self._preflight_suppressed = self._resume_card_covers(self._preflight_dir)
        head = "" if self._preflight_suppressed else headline
        text = " ".join(p for p in (head, self._preflight_coverage,
                                    self._scope_line()) if p)
        self._preflight_label.setText(text)
        # An empty report is not a report. With its account of the run
        # suppressed and no coverage line arrived yet, there is nothing in the
        # card but its buttons, and a bordered strip holding two buttons and no
        # sentence reads as a rendering fault. It comes up when the scan lands.
        banner.setVisible(bool(text) and self._preflight_is_wanted())

    def _scope_line(self) -> str:
        """What this run will do about images added since the last one.

        The sentence is ``ScopePlan.preview`` verbatim: the same verdict and
        the same justification the run log prints, in the tense that is true
        before the fold happens. Rendering the log's own past-tense ``reason``
        here had the readout announce "Appended 3 loose image(s)" while nothing
        had been written — a claim about work not yet done, which is the one
        thing a preview must never make.
        """
        # The planner's *resolved* input directory, never the raw text the user
        # typed. Loose images sit beside the well archives inside ``in/``, so a
        # planner pointed at the folder that merely *contains* ``in/`` finds no
        # archives, reports nothing to fold in, and the readout goes silent on
        # a run that is about to fold in a dozen files. The coverage scan had
        # the same bug; fixing one and not the other is how a sweep half-lands.
        in_dir = getattr(self, "_preflight_input", "")
        if not in_dir:
            return ""
        try:
            opts = self._collect_run_options()
        except Exception:
            swallowed("collecting run options for the pre-run readout")
            return ""
        with swallow("planning the run scope for the pre-run readout"):
            plan = plan_incremental_scope(opts, Path(in_dir))
            return plan.preview
        return ""

    # ── tier two: real coverage, off the GUI thread ──────────────────────
    def _start_coverage_scan(self) -> None:
        """Walk the output folder on a worker and report what it covers.

        Daemon thread: a scan still walking a slow network volume must not keep
        the app from closing, and its result is a line of text — losing it
        costs the user nothing they cannot get by looking again.
        """
        target = self._preflight_dir
        if not target:
            return
        if self._preflight_scanning:
            # One in flight already. Note that the answer wanted now is not the
            # one being computed and let its completion start the next, rather
            # than running two walks of the same plate at once.
            self._preflight_scan_queued = self._preflight_scanning != target
            return
        try:
            opts = self._collect_run_options()
        except Exception:
            swallowed("collecting run options for the coverage scan")
            return
        in_dir = getattr(self, "_preflight_input", "")
        if not in_dir:
            return
        signals = self._preflight_signals

        def _work() -> None:
            block = None
            try:
                block = _scan_coverage(Path(in_dir), Path(target), opts)
            except Exception:
                swallowed(f"scanning {target} for the pre-run readout")
            try:
                signals.done.emit(target, block)
            except RuntimeError:
                # The pane was closed while this scan was walking the plate, so
                # the receiver's C++ half is gone. There is nothing to deliver
                # the answer to and nothing to repair — but the emit must not
                # raise out of a daemon thread, where it would surface as an
                # unhandled traceback with no context.
                swallowed(f"pre-run scan of {target} outlived its window")

        self._preflight_scanning = target
        self._preflight_scan_queued = False
        try:
            threading.Thread(target=_work, name="aw-preflight-scan",
                             daemon=True).start()
        except RuntimeError:
            # No thread could be started. Take the in-flight marker back down
            # by hand — nothing is going to emit the signal that normally
            # clears it, and leaving it set would refuse every later scan for
            # the life of the pane.
            self._preflight_scanning = ""
            swallowed(f"could not start the pre-run scan of {target}")

    def _on_coverage_scanned(self, output_dir: str, block: object) -> None:
        """Fold a finished scan into the banner, if it still describes it.

        A scan that lands after the user has typed on is discarded rather than
        shown against the wrong folder — the slowest thing this does is also
        the one whose answer goes stale.
        """
        # Cleared before anything can return: this is the only place the
        # in-flight marker comes down, so an early return here would leave the
        # pane believing a scan was still running and refuse every later one.
        self._preflight_scanning = ""
        if self._preflight_scan_queued:
            self._preflight_scan_queued = False
            self._start_coverage_scan()
        if output_dir != self._preflight_dir or not isinstance(block, dict):
            return
        totals = block.get("totals") or {}
        intent = int(totals.get("intent") or 0)
        covered = int(totals.get("covered_any") or 0)
        complete = int(totals.get("complete") or 0)
        unreadable = int(totals.get("unreadable") or 0)
        # The plate's size decides whether there is anything to say at all, so
        # it is asked for once and used for both the gate and the fraction. It
        # counts the wells that could not be read: a scan where *every* well
        # raised used to return here without a word, which is the worst case of
        # the thing this readout exists to report.
        plate = completeness.plate_total(totals)
        if not plate:
            return
        # The denominator is the plate, not the subset the scan managed to
        # read, and it comes from ``completeness.plate_total`` because three
        # other surfaces print the same fraction and one pane holding two
        # plate sizes is worse than all of them being wrong the same way
        # (CLAUDE.md §7.5 #5).
        head = f"{complete} of {plate} well(s) are complete."
        # A scan that gave up part-way reported its partial totals as though
        # they were the plate's. `scan_run` sets `partial_scan` for exactly
        # this — one unreadable well must not cost the record for the other
        # 95, so it keeps going and says so — and two other surfaces already
        # read the flag (the Home plate and the run log). This line did not,
        # so "94 of 96 well(s) are complete" could mean two wells short or two
        # wells unreadable, and the user is about to decide whether to re-run
        # on the strength of it.
        if block.get("partial_scan"):
            # Named, not merely hedged. "Some wells" left the user unable to
            # tell two unreadable wells from twenty, and the all-unreadable
            # case — every well failed, so ``wells`` is 0 — used to return
            # above without a word, which is the worst instance of the very
            # thing this line exists to report.
            how_many = (f"{unreadable} well(s) could not be read"
                        if unreadable else "Some wells could not be read")
            self._preflight_coverage = (
                f"{head} {how_many}, so this count is a floor rather than the "
                f"plate's — check the log for what the scan skipped before "
                f"deciding whether to re-run.")
            self._render_preflight(getattr(self, "_preflight_headline_text", ""))
            return
        # The fraction is only printed when it is one. ``covered_any`` counts
        # the output's field groups and ``intent`` the input's, so more covered
        # than intended is not a plate that is more than finished — it is the
        # filename schema failing to parse one side of the comparison, and a
        # ratio over 1 is the software knowing that and saying it anyway. Say
        # what is wrong instead, because the run about to start reads those
        # same names.
        if intent and covered > intent:
            self._preflight_coverage = (
                f"{head} Coverage could not be compared — the filename schema "
                f"does not parse this dataset's input and output names the "
                f"same way, so check it before running.")
        elif intent:
            self._preflight_coverage = (
                f"{head} {covered:,} of {intent:,} field group(s) already "
                f"covered, so a re-run computes the rest.")
        else:
            # No measurable intent: an input the scan could not read. Report
            # the wells and stop rather than inventing a denominator.
            self._preflight_coverage = head
        self._render_preflight(getattr(self, "_preflight_headline_text", ""))

    # ── the offer ────────────────────────────────────────────────────────
    def _load_previous_settings(self) -> None:
        """Fill the form from the run this folder already holds.

        Journal first, sidecar second. The journal carries the whole
        ``RunSpec`` — every form field, exactly as the user set it — while the
        sidecar carries what the pipeline was passed, which is most of it and
        is all a dataset produced before the journal existed can offer.
        """
        out_dir = Path(self._preflight_dir) if self._preflight_dir else None
        if out_dir is None:
            return
        info, source = _previous_settings(out_dir)
        if not info:
            self._log_line(
                "[info] Nothing to load — this folder records no run settings.\n",
                "INFO")
            return
        dropped = [k for k in _SCOPE_KEYS if k in info]
        applied = {k: v for k, v in info.items() if k not in _SCOPE_KEYS}
        try:
            # Wrapped as ``execution_options``, which is the one shape
            # ``_pinfo_flat`` passes through whole. Handing it these keys at the
            # top level instead drops every one it does not name there — among
            # them ``filename_schema`` and ``filename_sep``, so the form kept
            # its default schema, the pre-run scan then parsed the plate's
            # names by the wrong field order, and the coverage line reported a
            # dataset it had mis-read. The journal's ``opts`` are the Analyze
            # form's own dict (``RunSpec.to_json`` → ``_collect_run_options``),
            # not argparse's namespace — but its keys are the argparse dests,
            # which is what makes ``execution_options`` the right block for it.
            # The few form-only keys (``raw``, ``scratch_dir``,
            # ``remote_batch_size``) pass through it untouched.
            self._apply_pipeline_info({"execution_options": applied},
                                      set_input_path=False)
        except Exception:
            swallowed("applying a previous run's settings")
            self._log_line(
                "[warn] Some fields could not be restored from the previous "
                "run; check the form before running.\n", "WARNING")
            return
        note = (f" Reprocessing options ({', '.join(dropped)}) were left as you "
                f"have them." if dropped else "")
        self._log_line(
            f"[info] Loaded the settings from this folder's {source}.{note}\n",
            "INFO")

    def _dismiss_preflight(self) -> None:
        """Stop reporting on this folder until the user points somewhere else."""
        self._preflight_dismissed = self._preflight_dir
        banner = getattr(self, "_preflight_banner", None)
        if banner is not None:
            banner.setVisible(False)


# ── module-level helpers, kept out of the widget so they can be run ─────────

def _same(a: str, b: str) -> bool:
    from services.suspend_record import same_dir
    return same_dir(a, b)




def _as_sidecar_polarity(opts: dict) -> dict:
    """Restate a form ``opts`` dict in the polarity the applier reads.

    Two settings are spelled one way in the form and its journal spec and the
    other way in the sidecar: the form carries ``auto_threshold`` and
    ``run_check`` as positives, and the pipeline records the negatives it was
    given on the command line, ``no_auto_threshold`` and ``no_run_check``.
    ``_apply_pinfo_settings`` reads the sidecar's spelling.

    So a journal-sourced settings load handed the applier a dict that named
    neither key it was looking for, and both silently kept whatever the form
    happened to be showing. "Load these settings" restored eighteen fields and
    quietly left two alone — the two that decide whether the plate gets a gate
    and whether the run audits itself.

    Translated here, at the one place the journal shape enters, rather than by
    teaching the applier a second vocabulary: the applier has one source of
    truth for what a settings key is called, and adding a synonym to it is how
    the next pair comes to disagree.
    """
    out = dict(opts)
    for positive, negative in (("auto_threshold", "no_auto_threshold"),
                               ("run_check", "no_run_check")):
        if positive in out and negative not in out:
            out[negative] = not bool(out[positive])
    return out


def _previous_settings(out_dir: Path) -> tuple[dict, str]:
    """The form settings this folder records, and where they came from."""
    journal = run_journal.read_journal(out_dir)
    spec = (journal.spec if journal is not None else None) or {}
    opts = spec.get("opts") if isinstance(spec.get("opts"), dict) else None
    if opts:
        return _as_sidecar_polarity(opts), "run journal"
    info_path = out_dir / "pipeline_info.json"
    if info_path.is_file():
        import json
        try:
            info = json.loads(info_path.read_text())
        except (OSError, ValueError):
            return {}, ""
        if isinstance(info, dict):
            from analyze.pinfo import AnalyzePipelineInfoMixin
            return AnalyzePipelineInfoMixin._pinfo_flat(info), "pipeline_info.json"
    return {}, ""


def _scan_coverage(input_dir: Path, output_dir: Path, opts: dict) -> dict | None:
    """The ``run_check`` block this folder would produce right now.

    ``pipeline.completeness.scan_run`` is the single definition of "is this
    well complete", shared with the end-of-run check and Datasets → Validate.
    Calling it here rather than counting files is the whole reason the preview
    and the run can be trusted to agree.

    Module-level and taking plain paths so it can be exercised directly,
    without a widget and without a thread.
    """
    from pipeline.completeness import scan_run
    from services.remote_runner import input_well_containers

    schema = [f.strip() for f in str(opts.get("filename_schema") or "").split(":")
              if f.strip()]
    if not schema or not output_dir.is_dir():
        return None
    # The same walk the end-of-run completeness check uses. This is the
    # *denominator* the coverage line is measured against, so a second copy of
    # "which entries are wells" would not raise — it would quietly produce a
    # fraction nobody could reproduce.
    containers = input_well_containers(input_dir)
    return scan_run(
        in_containers=containers,
        output_dir=output_dir,
        schema=schema,
        sep=str(opts.get("filename_sep") or "_"),
        csv_prefix=str(opts.get("csv_prefix") or "measurements"),
        fov_field_present="fov" in [f.lower() for f in schema],
        mode="preflight",
    )
