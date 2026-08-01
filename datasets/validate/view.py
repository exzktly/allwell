"""Qt builder for the Validate & Repair Dataset page.

A dataset-folder picker, a **Check** / **Apply repairs** / **Complete this
run** button row, a live per-well health plate
(:class:`~datasets.validate.dashboard.ValidateDashboard`), and a findings
list with the raw scan log demoted to a collapsible checkbox — the same
Analyze-style status shape ``datasets/concatenate/view.py`` uses.

Pure view: it owns widgets and emits intent (``checkRequested``,
``applyRepairsRequested``, ``completeRunRequested``); the worker wiring lives
in :mod:`~datasets.validate.controller`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.path_field import PathField, folder_picker
from datasets._common.dataset_paths import preview_wells
from datasets.validate.dashboard import ValidateDashboard
from datasets.validate.validate_compute import DatasetReport


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


def _is_dataset(root: Path) -> str:
    """Return "" if *root* looks like a dataset, else a short reason --
    mirrors ``datasets/concatenate/view.py``'s own ``_is_dataset``. Validate's
    field was the one dataset-folder ``PathField`` in the whole pane with no
    validator at all: every other tool's dataset picker says up front when a
    folder is missing ``in``/``out``, while this one's Check button just
    stayed silently disabled with no inline reason."""
    if not (root / "in").is_dir() or not (root / "out").is_dir():
        return "Needs an 'in' and an 'out' folder."
    return ""


_SEVERITY_PREFIX = {"error": "✗", "warning": "⚠", "info": "ℹ"}


class ValidatePage(QWidget):
    """The Validate & Repair Dataset feature body."""

    checkRequested = Signal()
    applyRepairsRequested = Signal()
    completeRunRequested = Signal(str)
    queueRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset_root = ""
        self._running = False
        self._build_ui()

    def _build_ui(self) -> None:
        # Wrap in a scroll area — see datasets/concatenate/view.py's
        # _build_ui for why (the dashboard's plate map used to render
        # illegibly small at the app's default window height).
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(scroll)

        content = QWidget(scroll)
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Validate & Repair Dataset", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Check what an Analyze run actually finished, and repair the "
            "metadata a crashed or partial run can leave stale — never "
            "measurement data or images.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        blurb.setToolTip(
            "A crash or partial run can leave pipeline_info.json advertising "
            "more than out/ actually holds. Repairs touch only the "
            "availability lists, stale cell overrides, and leftover staging "
            "files."
        )
        outer.addWidget(blurb)

        self._dataset_field = PathField(
            "Dataset folder", pick=folder_picker("Select dataset folder"),
            validator=_is_dataset)
        self._dataset_field.changed.connect(self._on_dataset_changed)
        outer.addWidget(self._dataset_field)

        btn_row = QWidget(self)
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(theme.Spacing.sm)
        self._check_btn = QPushButton("▶  Check", self)
        self._check_btn.setProperty("variant", "primary")
        self._check_btn.setEnabled(False)
        self._check_btn.clicked.connect(self.checkRequested)
        bl.addWidget(self._check_btn)
        # Second, right after the primary action -- matching every other
        # Datasets tool's run-row ordering (Run, then +Queue, then any
        # secondary actions specific to this tool).
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Add this check to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        bl.addWidget(self._queue_btn)
        self._repair_btn = QPushButton("Apply repairs", self)
        self._repair_btn.setProperty("variant", "secondary")
        self._repair_btn.setEnabled(False)
        self._repair_btn.clicked.connect(self.applyRepairsRequested)
        bl.addWidget(self._repair_btn)
        self._complete_btn = QPushButton("Complete this run in Analyze", self)
        self._complete_btn.setProperty("variant", "secondary")
        self._complete_btn.setVisible(False)
        self._complete_btn.clicked.connect(
            lambda: self.completeRunRequested.emit(self._dataset_root))
        bl.addWidget(self._complete_btn)
        bl.addStretch(1)
        outer.addWidget(btn_row)

        self._build_status_section(outer)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        # Two distinct panels below the run row, each with its own header so
        # the "Clear" action can never be read as applying to the wrong one:
        # "Findings" (the dashboard plate + the per-issue list a Check
        # produces) and, only once the checkbox below reveals it, "Raw log"
        # (the worker's own line-by-line output). The button used to sit in
        # the Findings header while actually clearing the log underneath it
        # — pressing "Clear" while reading findings looked like it did
        # nothing, since the log it actually wiped was usually hidden.
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Findings", hdr))
        hl.addStretch(1)
        self._raw_log_cb = QCheckBox("Show raw log", hdr)
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        hl.addWidget(self._raw_log_cb)
        outer.addWidget(hdr)
        outer.addWidget(_hrule(self))

        self.dashboard = ValidateDashboard(self)
        # Not shown until a run actually starts — an idle status strip above
        # an empty log reads as "something is wrong" rather than "nothing
        # has happened yet". ValidateController reveals it in _run().
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard, 1)

        self._findings_list = QListWidget(self)
        self._findings_list.setObjectName("ValidateFindings")
        outer.addWidget(self._findings_list, 1)
        self._show_empty_findings_hint()

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)
        self._log_hdr = QWidget(self)
        log_hl = QHBoxLayout(self._log_hdr)
        log_hl.setContentsMargins(12, 6, 12, 6)
        log_hl.addWidget(QLabel("Raw log", self._log_hdr))
        log_hl.addStretch(1)
        self._clear_log_btn = QPushButton("Clear log", self._log_hdr)
        self._clear_log_btn.setProperty("variant", "secondary")
        self._clear_log_btn.clicked.connect(self.clear_log)
        log_hl.addWidget(self._clear_log_btn)
        outer.addWidget(self._log_hdr)
        self._status = QPlainTextEdit(self)
        self._status.setObjectName("ValidateStatus")
        self._status.setReadOnly(True)
        self._status.document().setMaximumBlockCount(10000)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText("Log will appear here.")
        outer.addWidget(self._status, 1)
        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        for w in (
            getattr(self, "_status", None), getattr(self, "_log_sep", None),
            getattr(self, "_log_hdr", None),
        ):
            if w is not None:
                w.setVisible(bool(show))

    # ── public API used by the controller ────────────────────────────────────
    def dataset_path(self) -> str:
        return self._dataset_field.path()

    def set_dataset_path(self, path: str) -> None:
        # PathField.set_path() deliberately doesn't emit `changed` (only an
        # interactive Browse pick does), so a page populated this way —
        # QueueBridge.show_job is the only caller — never ran the handler that
        # enables "▶ Check" and paints the plate map. Selecting a queued
        # Validate job therefore filled in a perfectly valid dataset folder
        # and left the page inert: a disabled Check button that did nothing at
        # all when pressed, above a blank plate. Run the same handler an
        # interactive pick runs.
        self._dataset_field.set_path(path)
        self._on_dataset_changed()

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def clear_findings(self) -> None:
        self._show_empty_findings_hint()

    def _show_empty_findings_hint(self) -> None:
        # Lives inside the (otherwise large, blank) findings list itself,
        # not as a separate caption below it — an empty container that big
        # with no explanation inside it read as broken, the same class of
        # problem the dashboard's own hide-until-ready fix addressed.
        self._findings_list.clear()
        item = QListWidgetItem("Pick a dataset folder and press Check.")
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self._findings_list.addItem(item)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._check_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        if running:
            # Only force-disable while starting a run — leave it alone on
            # stop, since show_report() (driven by the worker's `succeeded`,
            # which the QThread's own `finished` always fires after) has
            # already set the correct enabled state for the fresh report.
            self._repair_btn.setEnabled(False)
        self._check_btn.setText("Checking…" if running else "▶  Check")
        # Locked for the run's duration — picking a new dataset mid-run would
        # let the running worker's report land against a path the user has
        # already moved on from (ValidateController._on_succeeded uses the
        # worker's own report.root, not this field, but the field itself must
        # not drift out from under the user while a scan is in flight).
        # setLocked(), not setEnabled(False) — see PathField.setLocked.
        self._dataset_field.setLocked(running)

    def show_report(self, report: DatasetReport) -> None:
        self._dataset_root = str(report.root)
        self._findings_list.clear()
        if not report.findings:
            item = QListWidgetItem("No issues found.")
            self._findings_list.addItem(item)
        for f in report.findings:
            prefix = _SEVERITY_PREFIX.get(f.severity, "•")
            text = f"{prefix} {f.message}"
            item = QListWidgetItem(text)
            self._findings_list.addItem(item)
        self._repair_btn.setEnabled(report.repairable)
        # A greyed button with findings still listed above it looks broken
        # ("it found problems, why won't it fix them?") with nothing on the
        # page saying why not — state it directly rather than leaving the
        # user to guess between "nothing to repair" and "can't repair this".
        if report.findings and not report.repairable:
            self._repair_btn.setToolTip("These findings can't be repaired automatically.")
        else:
            self._repair_btn.setToolTip("")
        needs_completion = any(w.missing for w in report.wells)
        self._complete_btn.setVisible(needs_completion)
        self._check_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())

    # ── internals ────────────────────────────────────────────────────────────
    def _inputs_ready(self) -> bool:
        return self._dataset_field.is_valid()

    def _on_dataset_changed(self) -> None:
        self._check_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())
        self._repair_btn.setEnabled(False)
        self._complete_btn.setVisible(False)
        self._show_empty_findings_hint()
        self._update_status_preview()

    def _update_status_preview(self) -> None:
        # Reveals the plate once there's a real dataset to show, seeded with
        # its actual wells, instead of hiding until Check starts (an idle
        # status strip above an empty findings list used to read as "nothing
        # has happened yet" either way, so there was nothing lost by
        # answering the wells question as soon as it has a real answer).
        if self._running:
            return
        self.dashboard.reset()
        if not self._inputs_ready():
            self.dashboard.setVisible(False)
            return
        wells = preview_wells(self.dataset_path())
        self.dashboard.set_wells(wells)
        self.dashboard.set_headline(f"Ready — {len(wells)} well(s) to check.")
        self.dashboard.setVisible(True)
