"""Qt builder for the Subset / Split Dataset page.

Source + destination folder pickers, a well-plate selector, timepoint and FOV
checkbox rows seeded from the source dataset's own ``available_timepoints``/
``available_fovs``, and a compact status strip
(:class:`~datasets.subset.dashboard.SubsetDashboard` — headline, overall bar,
a per-well-state counts rail, and a colour legend, but **no plate map of its
own**) with the raw log demoted to a collapsible checkbox. Run progress is
painted directly onto the well-plate selector above instead of a second
plate map — see :mod:`~datasets.subset.controller`.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``, ``sourceChanged``); the worker wiring lives in
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
from widgets._support import set_variant
from widgets.flow_layout import FlowLayout
from widgets.path_field import PathField, folder_picker
from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector
from datasets.subset.dashboard import SubsetDashboard
from datasets.subset.subset_compute import (
    DatasetPaths,
    destination_has_marker,
    destination_has_unrecognized_content,
    destination_is_complete_dataset,
)


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


def _is_dataset(root: Path) -> str:
    if not (root / "in").is_dir() or not (root / "out").is_dir():
        return "Needs an 'in' and an 'out' folder."
    return ""


def _is_destination(root: Path) -> str:
    """Return "" if *root* is usable as a subset destination, else a short
    reason — mirrors ``datasets/concatenate/view.py``'s own ``_is_destination``.
    Subset's destination field was the only one in the pane with no
    validator: a folder that already held a finished dataset just left the
    Subset button silently disabled with no inline reason, and
    ``build_subset_plan`` (``subset_compute.py``) already refuses exactly
    this at plan time — the field should say so before the user presses Run,
    not after."""
    dest = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    if destination_is_complete_dataset(dest):
        return "Already holds a finished dataset — choose an empty folder."
    if destination_has_unrecognized_content(dest) and not destination_has_marker(dest):
        return "Already has unrelated files — choose an empty folder."
    return ""


class SubsetPage(QWidget):
    """The Subset / Split Dataset feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInReviewRequested = Signal(str)
    sourceChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._running = False
        self._tp_checks: dict[str, QCheckBox] = {}
        self._fov_checks: dict[str, QCheckBox] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        # The page's content (fields, plate selector, timepoints/FOVs,
        # status strip, log) can exceed the window's default height
        # (1640x980 — allwell.py). A plain QVBoxLayout with no scroll area
        # just crushes whatever doesn't fit into whatever's left, which is
        # exactly what made the well-plate selector above render illegibly
        # small before this wrap existed — its cell size is computed from
        # its actual allotted width/height (well_plate_selector's
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
            "Destination folder", pick=folder_picker("Select destination folder"),
            validator=_is_destination)
        self._dest_field.changed.connect(self._on_input_changed)
        outer.addWidget(self._dest_field)

        outer.addWidget(QLabel("Wells", self))
        self._plate = WellPlateSelector(self)
        # The app-wide plate geometry (widgets/plate_geometry.py): the "picker"
        # role keeps this plate's own All / Invert / Clear row — this page has no
        # other well-selection buttons — and the helper adds the room that row
        # needs on top of the shared height cap, so the grid itself still renders
        # at exactly the cell scale every other plate map in the app uses.
        apply_plate_geometry(self._plate, role="picker")
        self._plate.selectionChanged.connect(self._on_plate_selection_changed)
        outer.addWidget(self._plate, 0, PLATE_ALIGNMENT)

        outer.addWidget(QLabel("Timepoints", self))
        # Matches Import Loose's own "Pick a folder to preview." -- an empty
        # checkbox row under a heading, with nothing said about why, read as
        # broken rather than "pick a source first".
        self._tp_hint = QLabel("Pick a source dataset to preview timepoints.", self)
        self._tp_hint.setObjectName("Muted")
        outer.addWidget(self._tp_hint)
        self._tp_host = QWidget(self)
        self._tp_flow = FlowLayout(self._tp_host)
        outer.addWidget(self._tp_host)

        outer.addWidget(QLabel("FOVs", self))
        self._fov_hint = QLabel("Pick a source dataset to preview FOVs.", self)
        self._fov_hint.setObjectName("Muted")
        outer.addWidget(self._fov_hint)
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
        # Hidden until the inputs are valid enough to answer "what would
        # this subset?" — _update_status_preview() reveals it seeded with
        # the current picker selection, and SubsetController.launch() takes
        # over from there once a run actually starts.
        self.dashboard.setVisible(False)
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
        self._running = running
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        self._run_btn.setText("Subsetting…" if running else "▶  Subset")
        # Locked alongside the plate (SubsetController._launch) — picking a
        # new source mid-run fires sourceChanged, which repaints the plate
        # for the new dataset while the old run's worker keeps executing in
        # the background; its still-arriving wellStage signals would then
        # paint the *previous* dataset's progress onto wells the user now
        # believes belong to the newly-selected one. setLocked(), not
        # setEnabled(False) — the source path is exactly what a user
        # watching a long run most wants to check, and the latter dims it
        # to the lowest-contrast text on the page for no functional reason
        # (the path display is already read-only; only Browse needs locking).
        self._src_field.setLocked(running)
        self._dest_field.setLocked(running)
        if running:
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")

    def show_open_in_review(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(True)
        # Promoted to the primary action on success, demoting Subset — see
        # ConcatenatePage.show_open_in_review for the reasoning.
        set_variant(self._open_btn, "primary")
        set_variant(self._run_btn, "secondary")

    def revalidate_fields(self) -> None:
        self._src_field.revalidate()
        self._dest_field.revalidate()
        self._refresh_run_enabled()

    # ── internals ────────────────────────────────────────────────────────────
    def _on_plate_selection_changed(self, wells: list[str]) -> None:
        # A dedicated pre-run tint, not the picker's own default multi-select
        # look -- WellPlateSelector's default "selected" rendering cycles a
        # rank-based rainbow trace colour per selection order, and this same
        # widget is also where SubsetController paints post-run state
        # colours (pending/copying/done/empty — the last of which is
        # theme.Colors.accent, the exact hue a plain unselected-but-enabled
        # well already renders in a faint tint by default). accent_dim reads
        # as "chosen, not yet run" without colliding with either.
        self._plate.clearWellColors()
        for w in wells:
            self._plate.setWellColor(w, theme.Colors.accent_dim)
        self._refresh_run_enabled()
        self._update_status_preview()

    def _on_source_changed(self) -> None:
        self._open_btn.setVisible(False)
        set_variant(self._run_btn, "primary")
        self._reload_source_metadata()
        self._refresh_run_enabled()
        self._update_status_preview()
        self.sourceChanged.emit()

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
        self._rebuild_checks(self._tp_flow, self._tp_checks, timepoints, self._tp_hint)

        fovs = sorted({str(f).strip() for f in info.get("available_fovs", []) or [] if str(f).strip()})
        self._rebuild_checks(self._fov_flow, self._fov_checks, fovs, self._fov_hint)

    def _rebuild_checks(
        self, flow: FlowLayout, store: dict, labels: list[str], hint: QLabel,
    ) -> None:
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
            cb.toggled.connect(self._on_input_changed)
            flow.addWidget(cb)
            store[label] = cb
        hint.setVisible(not labels)

    def _inputs_ready(self) -> bool:
        return (
            self._src_field.is_valid()
            and self._dest_field.is_valid()
            and self.destination_path() != self.source_path()
            and bool(self.selected_wells())
            and bool(self.selected_timepoints() or not self._tp_checks)
            and bool(self.selected_fovs() or not self._fov_checks)
        )

    def _on_input_changed(self, *_args) -> None:
        self._refresh_run_enabled()
        self._update_status_preview()

    def _refresh_run_enabled(self) -> None:
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())

    def _update_status_preview(self) -> None:
        # The status strip (no plate map of its own — see SubsetDashboard's
        # own docstring) used to stay hidden until Run was pressed; reveal it
        # as soon as there's a real answer to "what would this subset?" —
        # the current picker selection — instead of gating it on the run
        # having actually started. Deliberately NOT called from
        # _refresh_run_enabled(): revalidate_fields() (called after a run
        # finishes, to refresh the fields' inline validity hints) also goes
        # through that function, and by then self._running is already False
        # -- calling this from there would immediately overwrite the just-
        # finished run's own result colours/headline with a plain
        # pending-coloured "Ready" preview. Wired instead to the specific
        # interactive-change handlers below, none of which fire mid-run
        # (source/dest fields and the plate are all locked for the run's
        # duration) or as part of run completion.
        if self._running:
            return
        if not self._inputs_ready():
            self.dashboard.setVisible(False)
            return
        wells = self.selected_wells()
        self.dashboard.reset()
        self.dashboard.set_wells(wells)
        self.dashboard.set_headline(f"Ready — {len(wells)} well(s) selected.")
        self.dashboard.setVisible(True)
