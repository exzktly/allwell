"""What stops the Analyze form from running, said where the user is looking.

**Every problem at once, beside Run, in the section it is about.** The form's
checks used to stop at the first problem and raise it in a modal dialog: fix
it, press Run, meet the next one. Now :meth:`AnalyzeRunProblemsMixin._run_problems`
asks every check and returns all of them, each tagged with the section that
holds the control to change. The run footer shows them under the Run button —
continuously, in a quiet tone while the form is being filled in, and in the
error colour once Run (or + Queue) has been pressed with them outstanding —
and pressing Run with a problem open unfolds that problem's section and
scrolls to it rather than opening a dialog.

The checks themselves are the ones the dialogs reported, in the same order and
with the same words, so the order of the list is the order a user fixes them
in. *for_queue* defers what a queued job cannot know yet (its input may not
exist, or be blank until a chained job fills it); *live* is the continuous
check, which reads nothing from disk — no packed-file scan, no look inside the
input for a blank or control well — so it can run on a timer. Those disk checks
are asked when Run is pressed.

``_last_run_problems`` keeps what the last Run / + Queue press reported, as
``(title, message)`` pairs, for whatever wants to read it after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer

from analyze.domain.fields import _nanowell_option_errors, _numeric_option_errors

#: How often the continuous check runs while the pane is on screen. Fast enough
#: to follow typing, slow enough to cost nothing measurable.
_LIVE_CHECK_MS = 700

#: Which section a numeric field's error belongs to, by the field label the
#: error starts with (``analyze.domain.fields``).
_NUMERIC_SECTIONS = (
    (("Segmentation-channel radius", "Other channels' radius",
      "Segmentation small px", "Other small px", "Mesh"), "Image correction"),
    (("Threads per worker", "Wells in parallel", "Max tile size"), "Advanced"),
    (("Batch size",), "Remote options"),
    (("QC ", "Out-of-focus"), "Measurements & filters"),
    (("Cellpose", "StarDist"), "Segmentation"),
)


@dataclass(frozen=True)
class RunProblem:
    """One reason the form cannot run: where, what kind, and what to do."""

    section: str
    title: str
    message: str


def _numeric_section(message: str) -> str:
    for prefixes, section in _NUMERIC_SECTIONS:
        if message.startswith(prefixes):
            return section
    return ""


class AnalyzeRunProblemsMixin:
    """Collects, shows and points at the form's run problems."""

    def _run_problems(self, *, for_queue: bool = False,
                      live: bool = False) -> list[RunProblem]:
        """Every reason the form cannot run now, in the order to fix them."""
        problems: list[RunProblem] = []
        raw = self._input_edit.text().strip()
        detect = self._auto_detect_on()
        if not raw and not for_queue:
            problems.append(RunProblem("Data", "Input Error", "No input folder selected."))
        # The Run button asks everything of whatever the field names; only a
        # queued job may name a folder not written yet.
        input_known = bool(raw) and (not for_queue or Path(raw).exists())
        if input_known and not live:
            packed_hint = self._packed_input_hint()
            if packed_hint is not None:
                problems.append(RunProblem("Data", "Packed File Input", packed_hint))
        schema_errors = [] if detect else self._schema_errors()
        if schema_errors:
            problems.append(RunProblem(
                "Filename schema", "Schema Error",
                "Invalid filename schema:\n\n" + "\n".join(schema_errors)))
        if not detect and not self._fluor_tokens_list():
            problems.append(RunProblem("Channels", "Channel Error",
                                       "At least one channel is required."))
        seg_errors = self._segmentation_validation_errors(channels=not detect)
        if seg_errors:
            problems.append(RunProblem("Segmentation", "Segmentation Error",
                                       "\n".join(seg_errors)))
        # Numeric fields — a bad value is refused at the boundary rather than
        # silently dropping the flag or dividing by zero downstream.
        opts = self._collect_run_options()
        nanowell_numeric = set(_nanowell_option_errors(opts))
        # One problem per section, each listing every bad number in it — the
        # way the dialog listed them all — so a section is opened once.
        by_section: dict[str, list[str]] = {}
        for message in _numeric_option_errors(opts):
            section = "Nanowell array" if message in nanowell_numeric else _numeric_section(message)
            by_section.setdefault(section, []).append(message)
        for section, messages in by_section.items():
            problems.append(RunProblem(section, "Input Error", "\n".join(messages)))
        # The parts of the well checks that parse the input's filenames cannot
        # be asked of an input that is not there yet, of a schema detection is
        # about to replace, of a schema that does not parse, or on the timer.
        check_input = input_known and not detect and not live and not schema_errors
        for section, title, check in (
                ("Image correction", "Background Well Error", self._background_well_validation_error),
                ("Auto-threshold", "Control Well Error", self._auto_threshold_validation_error),
                ("Nanowell array", "Nanowell Error", self._nanowell_validation_error)):
            message = check(check_input=check_input)
            if message:
                problems.append(RunProblem(section, title, message))
        return problems

    # ── the footer line ─────────────────────────────────────────────────────
    def _show_run_problems(self, problems: list[RunProblem], *, urgent: bool) -> None:
        """Write *problems* under the Run button: the first in full, a count
        of the rest, every one in the tooltip. *urgent* draws it in the error
        colour (Run was pressed); otherwise it is the quiet running list."""
        label = getattr(self, "_run_problems_lbl", None)
        if label is None:
            return
        self._shown_run_problems = list(problems)
        if not problems:
            self._set_status(label, "")
            return
        first = problems[0].message.split("\n\n")[0].strip()
        more = len(problems) - 1
        text = first + (f"  (+{more} more)" if more else "")
        self._set_status(label, text, error=urgent)
        label.setToolTip("\n\n".join(
            f"{p.section or p.title}: {p.message}" for p in problems))

    def _report_run_problems(self, problems: list[RunProblem]) -> None:
        """Run or + Queue was pressed: show the problems in the error colour and
        take the user to the first one."""
        self._run_attempted = bool(problems)
        self._last_run_problems = [(p.title, p.message) for p in problems]
        self._show_run_problems(problems, urgent=True)
        if problems:
            self._go_to_run_problem(problems[0])

    def _go_to_run_problem(self, problem: RunProblem | None = None) -> None:
        """Unfold the section *problem* is about and scroll it into view."""
        if problem is None:
            shown = getattr(self, "_shown_run_problems", None)
            if not shown:
                return
            problem = shown[0]
        if not problem.section:
            return
        self.reveal_section(problem.section)
        container = getattr(self, "_section_widgets", {}).get(problem.section)
        scroll = getattr(self, "_left_sidebar", None)
        if container is not None and scroll is not None and container.isVisibleTo(scroll):
            QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(container, 0, 24))

    # ── the continuous check ────────────────────────────────────────────────
    def _start_live_run_checks(self) -> None:
        """Re-ask the disk-free checks on a timer while the pane is on screen,
        and redraw the folded sections' summaries with them."""
        self._run_attempted = False
        self._live_check_timer = QTimer(self)
        self._live_check_timer.setInterval(_LIVE_CHECK_MS)
        self._live_check_timer.timeout.connect(self._live_run_check)
        self._live_check_timer.start()
        self._live_run_check()

    def _live_run_check(self) -> None:
        if not self.isVisible() or getattr(self, "_running", False):
            return
        from core.safe import swallow
        with swallow("checking the Analyze form"):
            problems = self._run_problems(live=True)
            # Once Run has been pressed, the list stays in the error colour
            # until it is empty: the user is fixing it, not filling in a form.
            self._show_run_problems(problems, urgent=bool(problems) and self._run_attempted)
            if not problems:
                self._run_attempted = False
            self.refresh_section_summaries()
