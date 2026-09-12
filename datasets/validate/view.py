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


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from widgets.path_field import PathField, folder_picker
from datasets.core.dataset_paths import preview_wells
from datasets.validate.dashboard import ValidateDashboard
from datasets.validate.validate_compute import DatasetReport, wells_needing_force
from datasets.core.dataset_fields import is_dataset




_is_dataset = is_dataset


_SEVERITY_PREFIX = {"error": "✗", "warning": "⚠", "info": "ℹ"}


class ValidatePage(LogPanelMixin, QWidget):
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
        self._run_btn = QPushButton("▶  Check", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.checkRequested)
        bl.addWidget(self._run_btn)
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
        self.build_log_header(outer, "Findings", clear=False)

        self.dashboard = ValidateDashboard(self)
        # Not shown until a run actually starts — an idle status strip above
        # an empty log reads as "something is wrong" rather than "nothing
        # has happened yet". ValidateController reveals it in _run().
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        # Hidden until a check produces something to put in it. An empty
        # findings list is not a small widget: it carries a layout stretch and
        # a panel border, so before a check it drew a page-height bordered box
        # holding one line of text. That line said "Pick a dataset folder and
        # press Check", which stayed exactly as it was after the user picked
        # one — the single largest thing on the page, describing a step they
        # had already completed. Showing nothing says the same thing honestly
        # and takes no room; the run row above is where the state actually
        # reads (Check enables the moment the folder is valid, and the
        # dashboard appears beside it with the wells a check would cover).
        self._findings_list = QListWidget(self)
        self._findings_list.setObjectName("ValidateFindings")
        self._findings_list.setVisible(False)
        outer.addWidget(self._findings_list, 1)

        self.build_log_tail(outer, object_name="ValidateStatus",
                            placeholder="Log will appear here.", log_header="Raw log")
        # The same trailing stretch every other Datasets page carries, and for
        # the same reason: `QScrollArea.setWidgetResizable(True)` grows this
        # content widget to fill the viewport, and with the findings list and
        # the raw log both hidden nothing else in the column would absorb the
        # leftover height — the dashboard would, turning into a growing dead
        # zone below its own plate. A trailing stretch outranks every 0-stretch
        # item above it, so the slack collects as blank page background at the
        # bottom instead. The other five pages add theirs in _build_ui after
        # this section returns; Validate's belongs here, at the end of its own.
        outer.addStretch(1)

    # ── public API used by the controller ──────────────────────────────
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

    def clear_findings(self) -> None:
        self._hide_findings()

    def show_failure(self, message: str) -> None:
        """Replace the findings with why the check failed, and withdraw the
        offer to complete a run.

        A failed check used to reset the list to an empty state — which says
        nothing happened, directly under a red "Failed" headline and *next to a
        still-visible* "Complete this run in Analyze" button left over from an
        earlier successful check. That button then handed Analyze a dataset
        root whose sidecar had just proved unreadable, so the settings could
        not travel with it and the user landed on an Analyze tab that looked
        untouched. The failure is now what the list says, and the completion
        offer goes with the report it belonged to.
        """
        self._dataset_root = ""
        self._findings_list.clear()
        item = QListWidgetItem(f"✗ Check failed — {message}")
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self._findings_list.addItem(item)
        self._show_findings()
        self._repair_btn.setEnabled(False)
        self._complete_btn.setVisible(False)

    def show_note(self, message: str) -> None:
        """Add a line to the findings list without disturbing what's there.

        For something the user must see that isn't a scan finding — the
        completion handoff reporting that a dataset's settings could not be
        read. Writing that to the raw log alone hid it: the log is behind a
        checkbox that is off by default.
        """
        item = QListWidgetItem(message)
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self._findings_list.addItem(item)
        self._show_findings()

    def _hide_findings(self) -> None:
        """Empty the findings list and take it off the page entirely.

        There is no empty-state text: a placeholder here is a page-height
        bordered box saying one thing, and the only thing it had to say was
        an instruction the run row above already carries.
        """
        self._findings_list.clear()
        self._findings_list.setVisible(False)

    def _show_findings(self) -> None:
        """Reveal the list for the rows a caller is about to add to it."""
        self._findings_list.setVisible(True)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        if running:
            # Only force-disable while starting a run — leave it alone on
            # stop, since show_report() (driven by the worker's `succeeded`,
            # which the QThread's own `finished` always fires after) has
            # already set the correct enabled state for the fresh report.
            self._repair_btn.setEnabled(False)
        self._run_btn.setText("Checking…" if running else "▶  Check")
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
        self._show_findings()
        self._repair_btn.setEnabled(report.repairable)
        # A greyed button with findings still listed above it looks broken
        # ("it found problems, why won't it fix them?") with nothing on the
        # page saying why not — state it directly rather than leaving the
        # user to guess between "nothing to repair" and "can't repair this".
        if report.findings and not report.repairable:
            self._repair_btn.setToolTip("These findings can't be repaired automatically.")
        else:
            self._repair_btn.setToolTip("")
        # Missing (fov, timepoint) pairs are not the only thing a completion
        # run fixes. A well with images and no measurements CSV has no missing
        # pairs at all — its own output archive covers them — yet it is the one
        # kind of well an incremental run can never repair on its own, so the
        # offer has to be on screen for a dataset whose only fault is that.
        needs_completion = (
            any(w.missing for w in report.wells) or bool(wells_needing_force(report))
        )
        self._complete_btn.setVisible(needs_completion)
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())

    # ── internals ────────────────────────────────────────────────────────────
    def _inputs_ready(self) -> bool:
        return self._dataset_field.is_valid()

    def _on_dataset_changed(self) -> None:
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())
        self._repair_btn.setEnabled(False)
        self._complete_btn.setVisible(False)
        self._hide_findings()
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
