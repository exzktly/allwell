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

from PySide6.QtCore import Signal
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
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.path_field import PathField, folder_picker
from datasets.validate.dashboard import ValidateDashboard
from datasets.validate.validate_compute import DatasetReport


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


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
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Validate & Repair Dataset", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Check what an Analyze run actually finished — a crash or a "
            "partial run can leave pipeline_info.json advertising more than "
            "out/ actually holds. Repairs here only touch metadata (the "
            "availability lists, stale cell overrides, leftover staging "
            "files) — never measurement data or images.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._dataset_field = PathField(
            "Dataset folder", pick=folder_picker("Select dataset folder"))
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
        self._repair_btn = QPushButton("Apply repairs", self)
        self._repair_btn.setProperty("variant", "secondary")
        self._repair_btn.setEnabled(False)
        self._repair_btn.clicked.connect(self.applyRepairsRequested)
        bl.addWidget(self._repair_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Add this check to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        bl.addWidget(self._queue_btn)
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
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Findings", hdr))
        hl.addStretch(1)
        self._raw_log_cb = QCheckBox("Show raw log", hdr)
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        hl.addWidget(self._raw_log_cb)
        clear_btn = QPushButton("Clear", hdr)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self.clear_log)
        hl.addWidget(clear_btn)
        outer.addWidget(hdr)
        outer.addWidget(_hrule(self))

        self.dashboard = ValidateDashboard(self)
        outer.addWidget(self.dashboard, 1)

        self._findings_list = QListWidget(self)
        self._findings_list.setObjectName("ValidateFindings")
        outer.addWidget(self._findings_list, 1)

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)
        self._status = QPlainTextEdit(self)
        self._status.setObjectName("ValidateStatus")
        self._status.setReadOnly(True)
        self._status.document().setMaximumBlockCount(10000)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText("Log will appear here.")
        outer.addWidget(self._status, 1)
        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        for w in (getattr(self, "_status", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))

    # ── public API used by the controller ────────────────────────────────────
    def dataset_path(self) -> str:
        return self._dataset_field.path()

    def set_dataset_path(self, path: str) -> None:
        self._dataset_field.set_path(path)

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def set_running(self, running: bool) -> None:
        self._check_btn.setEnabled(not running and bool(self.dataset_path()))
        self._queue_btn.setEnabled(not running and bool(self.dataset_path()))
        if running:
            # Only force-disable while starting a run — leave it alone on
            # stop, since show_report() (driven by the worker's `succeeded`,
            # which the QThread's own `finished` always fires after) has
            # already set the correct enabled state for the fresh report.
            self._repair_btn.setEnabled(False)
        self._check_btn.setText("Checking…" if running else "▶  Check")

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
        needs_completion = any(w.missing for w in report.wells)
        self._complete_btn.setVisible(needs_completion)
        self._check_btn.setEnabled(bool(self.dataset_path()))
        self._queue_btn.setEnabled(bool(self.dataset_path()))

    # ── internals ────────────────────────────────────────────────────────────
    def _on_dataset_changed(self) -> None:
        self._check_btn.setEnabled(bool(self.dataset_path()))
        self._queue_btn.setEnabled(bool(self.dataset_path()))
        self._repair_btn.setEnabled(False)
        self._complete_btn.setVisible(False)
        self.dashboard.reset()
        self._findings_list.clear()
