"""Qt builder for the Subset / Split Dataset page.

Source + destination folder pickers, a well-plate selector, timepoint and FOV
checkbox rows seeded from the source dataset's own ``available_timepoints``/
``available_fovs``, and the same Analyze-style status view (a live per-well
:class:`~datasets.subset.dashboard.SubsetDashboard` with the raw log demoted
to a collapsible checkbox) the other Datasets features use.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``); the worker wiring lives in
:mod:`~datasets.subset.controller`.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from util.timepoint import sort_key as tp_sort_key
from widgets.flow_layout import FlowLayout
from widgets.path_field import PathField, folder_picker
from widgets.well_plate_selector import WellPlateSelector
from datasets.subset.dashboard import SubsetDashboard


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


def _is_dataset(root: Path) -> str:
    if not (root / "in").is_dir() or not (root / "out").is_dir():
        return "Needs an 'in' and an 'out' folder."
    return ""


class SubsetPage(QWidget):
    """The Subset / Split Dataset feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInReviewRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._tp_checks: dict[str, QCheckBox] = {}
        self._fov_checks: dict[str, QCheckBox] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        # The page's content (fields, plate selector, dashboard, log) can
        # exceed the window's default height (1640x980 — allwell.py), and the
        # dashboard's own plate map sits at the very bottom. A plain
        # QVBoxLayout with no scroll area just crushes whatever doesn't fit
        # into whatever's left, which is exactly what made
        # SubsetDashboard._plate render illegibly small — its cell size is
        # computed from its actual allotted width/height (well_plate_selector's
        # _metrics()), and a squeezed few dozen pixels of height produces a
        # squeezed cell size regardless of how much width is available. Wrap
        # everything in a scroll area instead, mirroring the pattern
        # analyze_tab.py already uses for its own form panel.
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

        heading = QLabel("Subset / Split Dataset", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Copy a filtered slice of a dataset — chosen wells, timepoints "
            "and/or FOVs — into a new folder, leaving the source untouched. "
            "Timepoints keep their original hours; nothing is relabelled.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField(
            "Source dataset", pick=folder_picker("Select source dataset folder"),
            validator=_is_dataset)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)
        self._dest_field = PathField(
            "Destination folder", pick=folder_picker("Select destination folder"))
        self._dest_field.changed.connect(self._refresh_run_enabled)
        outer.addWidget(self._dest_field)

        outer.addWidget(QLabel("Wells", self))
        self._plate = WellPlateSelector(self)
        # Same cap as PlateProgress's own plate map (widgets/plate_progress.py)
        # and a matching width cap so this picker and the dashboard's status
        # plate at the bottom of this same page render at the same cell
        # scale, rather than this one spreading across the full page width
        # with dead space padding a tiny grid. Left-aligned since an
        # unconstrained QVBoxLayout item would otherwise stretch to fill —
        # and then immediately clip back down to maxWidth, floating it
        # wherever Qt's default alignment happens to land it.
        self._plate.setMaximumHeight(210)
        self._plate.setMaximumWidth(330)
        self._plate.selectionChanged.connect(self._refresh_run_enabled)
        outer.addWidget(self._plate, 0, Qt.AlignLeft)

        outer.addWidget(QLabel("Timepoints", self))
        self._tp_host = QWidget(self)
        self._tp_flow = FlowLayout(self._tp_host)
        outer.addWidget(self._tp_host)

        outer.addWidget(QLabel("FOVs", self))
        self._fov_host = QWidget(self)
        self._fov_flow = FlowLayout(self._fov_host)
        outer.addWidget(self._fov_host)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Subset", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        run_row_l.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Add this run to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        run_row_l.addWidget(self._queue_btn)
        run_row_l.addStretch(1)
        outer.addWidget(run_row, 0, Qt.AlignLeft)

        self._build_status_section(outer)

        self._open_btn = QPushButton("Open subset in Review", self)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(lambda: self.openInReviewRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)

        # A trailing stretch outranks every 0-stretch item above it for any
        # leftover space the QScrollArea's content widget picks up filling
        # the viewport, so slack collects as blank page background below
        # everything instead of inflating the dashboard's own plate map (the
        # dashboard has no maximumHeight of its own, and with the raw log
        # hidden by default there was nothing else to absorb the growth).
        outer.addStretch(1)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Subset Output", hdr))
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

        self.dashboard = SubsetDashboard(self)
        outer.addWidget(self.dashboard)

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)
        self._status = QPlainTextEdit(self)
        self._status.setObjectName("SubsetStatus")
        self._status.setReadOnly(True)
        self._status.document().setMaximumBlockCount(10000)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText("Status will appear here.")
        outer.addWidget(self._status, 1)
        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        for w in (getattr(self, "_status", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))

    # ── public API used by the controller ────────────────────────────────────
    def source_path(self) -> str:
        return self._src_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def selected_wells(self) -> list[str]:
        return list(self._plate.selectedWellIds())

    def selected_timepoints(self) -> list[str]:
        return [label for label, cb in self._tp_checks.items() if cb.isChecked()]

    def selected_fovs(self) -> list[str]:
        return [label for label, cb in self._fov_checks.items() if cb.isChecked()]

    def all_timepoints_selected(self) -> bool:
        return len(self.selected_timepoints()) == len(self._tp_checks)

    def all_fovs_selected(self) -> bool:
        return len(self.selected_fovs()) == len(self._fov_checks)

    def set_source_path(self, path: str) -> None:
        self._src_field.set_path(path)
        self._reload_source_metadata()
        self._refresh_run_enabled()

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def set_selected_wells(self, wells: list[str] | None) -> None:
        """``None`` keeps whatever _reload_source_metadata already selected
        (everything) — a queued job with no explicit well filter subsets
        nothing away rather than silently selecting zero wells."""
        if wells:
            self._plate.setSelectedWellIds(list(wells))

    def set_selected_timepoints(self, labels: list[str] | None) -> None:
        if labels is None:
            return
        wanted = set(labels)
        for label, cb in self._tp_checks.items():
            cb.setChecked(label in wanted)

    def set_selected_fovs(self, labels: list[str] | None) -> None:
        if labels is None:
            return
        wanted = set(labels)
        for label, cb in self._fov_checks.items():
            cb.setChecked(label in wanted)

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        self._run_btn.setText("Subsetting…" if running else "▶  Subset")
        if running:
            self._open_btn.setVisible(False)

    def show_open_in_review(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(True)

    def revalidate_fields(self) -> None:
        self._src_field.revalidate()
        self._dest_field.revalidate()
        self._refresh_run_enabled()

    # ── internals ────────────────────────────────────────────────────────────
    def _on_source_changed(self) -> None:
        self._open_btn.setVisible(False)
        self.dashboard.reset()
        self._reload_source_metadata()
        self._refresh_run_enabled()

    def _reload_source_metadata(self) -> None:
        src = self.source_path()
        info = {}
        if src:
            info_path = Path(src) / "out" / "pipeline_info.json"
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text())
                except (OSError, json.JSONDecodeError):
                    info = {}

        wells = []
        if src:
            in_dir = Path(src) / "in"
            if in_dir.is_dir():
                from util.well_token import canonical_well_label
                for entry in sorted(in_dir.iterdir()):
                    name = entry.stem if entry.suffix == ".zip" else entry.name
                    token = canonical_well_label(name)
                    if token:
                        wells.append(token)
        self._plate.setEnabledWells(wells or None)
        self._plate.setSelectedWellIds(wells)

        timepoints = sorted(
            {str(t).strip() for t in info.get("available_timepoints", []) or [] if str(t).strip()},
            key=tp_sort_key,
        )
        self._rebuild_checks(self._tp_flow, self._tp_checks, timepoints)

        fovs = sorted({str(f).strip() for f in info.get("available_fovs", []) or [] if str(f).strip()})
        self._rebuild_checks(self._fov_flow, self._fov_checks, fovs)

    def _rebuild_checks(self, flow: FlowLayout, store: dict, labels: list[str]) -> None:
        while flow.count():
            item = flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        store.clear()
        for label in labels:
            cb = QCheckBox(label, flow.parentWidget())
            cb.setChecked(True)
            cb.toggled.connect(self._refresh_run_enabled)
            flow.addWidget(cb)
            store[label] = cb

    def _inputs_ready(self) -> bool:
        return (
            self._src_field.is_valid()
            and bool(self.destination_path())
            and self.destination_path() != self.source_path()
            and bool(self.selected_wells())
            and bool(self.selected_timepoints() or not self._tp_checks)
            and bool(self.selected_fovs() or not self._fov_checks)
        )

    def _refresh_run_enabled(self) -> None:
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())
