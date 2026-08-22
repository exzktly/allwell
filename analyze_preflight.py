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

**Tier one is free** — one small JSON read of the journal — so it hangs
directly off the existing ``_refresh_output``. It says what the last run
against this folder did, and above all whether it *finished*: a journal still
open is a run the app was killed during, which nothing else on disk records.

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

**It renders; it does not phrase.** Every claim comes back verbatim from the
function that owns it — ``ScopePlan.reason`` for what the run will do about new
images, ``completeness``'s own counts for coverage, the journal's recorded
states for what happened. This is the rule
``tests/test_layout_readout_single_source.py`` already holds over the layout
line beside it, for exactly the same reason: the preview and the run must not
be able to disagree.

Held as a mixin rather than folded into ``analyze_tab.py`` for the reason
``analyze_suspend`` and ``analyze_progress`` were — that file sits against its
line budget, and this is a self-contained feature with one entry point.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from services import run_journal
from services.input_resolution_service import plan_incremental_scope
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
        ``analyze_tab_form``, which is against its line budget and whose every
        addition has to buy its place.
        """
        parent.layout().addWidget(self._build_resume_banner(parent))
        parent.layout().addWidget(self._build_preflight_banner(parent))

    def _build_preflight_banner(self, parent: QWidget) -> QWidget:
        """The strip that reports what is already in the output folder.

        Hidden until there is something to report, which is the normal state
        for a fresh plate — it costs a row of chrome only when it has something
        to say.
        """
        banner = QWidget(parent)
        banner.setObjectName("PreflightBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self._preflight_label = QLabel("", banner)
        self._preflight_label.setWordWrap(True)
        row.addWidget(self._preflight_label, 1)
        self._preflight_load_btn = QPushButton("Load these settings", banner)
        self._preflight_load_btn.setToolTip(
            "Fill the form with the settings that produced the output already "
            "in this folder. Reprocessing options are left as you have them.")
        self._preflight_load_btn.clicked.connect(self._load_previous_settings)
        row.addWidget(self._preflight_load_btn)
        dismiss = QPushButton("Dismiss", banner)
        dismiss.clicked.connect(self._dismiss_preflight)
        row.addWidget(dismiss)
        self._preflight_banner = banner
        banner.setVisible(False)

        self._preflight_signals = _ScanSignals()
        self._preflight_signals.done.connect(self._on_coverage_scanned)
        self._preflight_timer = QTimer(banner)
        self._preflight_timer.setSingleShot(True)
        self._preflight_timer.setInterval(_SETTLE_MS)
        self._preflight_timer.timeout.connect(self._start_coverage_scan)
        #: Output dir the visible report describes; guards a scan that lands
        #: after the user has moved on to a different folder.
        self._preflight_dir: str = ""
        self._preflight_dismissed: str = ""
        self._preflight_coverage: str = ""
        return banner

    # ── tier one: the journal, immediately ───────────────────────────────
    def refresh_preflight(self, plan) -> None:
        """Report on the folder *plan* resolves to, and queue the scan behind it.

        Takes the whole :class:`~services.input_resolution_service.InputPlan`
        rather than a path, because the scan needs the planner's **resolved**
        input directory and not the raw folder the user typed. Handing it the
        raw path made it read a directory holding ``in/`` and ``out/`` as
        though it were the plate: no well containers were found, so the field
        groups it counted came only from the output, and the readout printed
        more groups covered than the input was ever measured to hold.
        """
        banner = getattr(self, "_preflight_banner", None)
        if banner is None:
            return
        output_dir = getattr(plan, "output_dir", None) if plan is not None else None
        self._preflight_input = str(getattr(plan, "input_dir", "") or "")
        target = str(output_dir) if output_dir is not None else ""
        if target != self._preflight_dir:
            # A different folder: whatever the last scan said no longer applies.
            self._preflight_coverage = ""
        self._preflight_dir = target
        self._preflight_timer.stop()
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
        self._preflight_timer.start()

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
        return head + _well_tally(journal)

    def _render_preflight(self, headline: str) -> None:
        """Paint the banner, coverage line included when one has arrived."""
        banner = getattr(self, "_preflight_banner", None)
        if banner is None:
            return
        self._preflight_headline_text = headline
        self._preflight_label.setText(
            " ".join(p for p in (headline, self._preflight_coverage,
                                 self._scope_line()) if p))
        banner.setVisible(True)

    def _scope_line(self) -> str:
        """What this run will do about images added since the last one.

        The sentence is ``ScopePlan.reason`` verbatim — the same string the run
        log prints when it actually folds those images in — so the preview
        cannot describe a decision the run then does not make.
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
            return plan.reason
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
            signals.done.emit(target, block)

        threading.Thread(target=_work, name="aw-preflight-scan",
                         daemon=True).start()

    def _on_coverage_scanned(self, output_dir: str, block: object) -> None:
        """Fold a finished scan into the banner, if it still describes it.

        A scan that lands after the user has typed on is discarded rather than
        shown against the wrong folder — the slowest thing this does is also
        the one whose answer goes stale.
        """
        if output_dir != self._preflight_dir or not isinstance(block, dict):
            return
        totals = block.get("totals") or {}
        intent = int(totals.get("intent") or 0)
        covered = int(totals.get("covered_any") or 0)
        complete = int(totals.get("complete") or 0)
        wells = int(totals.get("wells") or 0)
        if not wells:
            return
        head = f"{complete} of {wells} well(s) are complete."
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
            # dataset it had mis-read. The journal's ``opts`` *are* a
            # ``vars(args)`` snapshot, which is exactly what that block holds.
            self._apply_pipeline_info({"execution_options": applied})
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


def _well_tally(journal: run_journal.RunJournal) -> str:
    """The per-well counts the run recorded, or ``""``.

    Reported, never derived: these are states the run wrote down as it went.
    They say nothing about whether the *output* is complete — that is
    ``pipeline.completeness``'s answer, and the coverage line carries it.
    """
    if not journal.wells:
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


def _previous_settings(out_dir: Path) -> tuple[dict, str]:
    """The form settings this folder records, and where they came from."""
    journal = run_journal.read_journal(out_dir)
    spec = (journal.spec if journal is not None else None) or {}
    opts = spec.get("opts") if isinstance(spec.get("opts"), dict) else None
    if opts:
        return dict(opts), "run journal"
    info_path = out_dir / "pipeline_info.json"
    if info_path.is_file():
        import json
        try:
            info = json.loads(info_path.read_text())
        except (OSError, ValueError):
            return {}, ""
        if isinstance(info, dict):
            from analyze_tab_pinfo import AnalyzePipelineInfoMixin
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
    from util.well_token import canonical_well_label

    schema = [f.strip() for f in str(opts.get("filename_schema") or "").split(":")
              if f.strip()]
    if not schema or not output_dir.is_dir():
        return None
    containers: dict[str, Path] = {}
    try:
        for path in sorted(input_dir.iterdir()):
            if path.name.startswith(".") or path.name.startswith("_"):
                continue
            stem = path.stem if path.suffix == ".zip" else path.name
            label = canonical_well_label(stem)
            if label and (path.is_dir() or path.suffix == ".zip"):
                containers[label] = path
    except OSError:
        containers = {}
    return scan_run(
        in_containers=containers,
        output_dir=output_dir,
        schema=schema,
        sep=str(opts.get("filename_sep") or "_"),
        csv_prefix=str(opts.get("csv_prefix") or "measurements"),
        fov_field_present="fov" in [f.lower() for f in schema],
        mode="preflight",
    )
