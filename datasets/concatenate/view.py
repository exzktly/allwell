"""Qt builder for the Concatenate Datasets page (the main viewer area).

One equation-style input row — dataset 1 (the base / earlier timepoints) +
dataset 2 (the one appended after) = a fresh destination — over an
Analyze-style status view: a live per-well :class:`~datasets.concatenate.dashboard.ConcatDashboard`
with the raw text log demoted to a collapsible "Show raw log" checkbox
(mirrors ``analyze_tab_form.py``'s ``_build_log``/``_on_toggle_raw_log``).

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``); the worker wiring lives in
:mod:`~datasets.concatenate.controller`. Colours/spacing come from the app QSS
and theme tokens — nothing hardcoded (CLAUDE.md §5).
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.path_field import PathField, folder_picker
from datasets._common.dataset_paths import preview_wells
from datasets.concatenate.concatenate_compute import (
    DatasetPaths,
    destination_has_marker,
    destination_has_unrecognized_content,
    destination_is_complete_dataset,
)
from datasets.concatenate.dashboard import ConcatDashboard


def _hrule(parent: QWidget) -> QFrame:
    """Full-width divider matching Analyze's section/log-header rules
    (``QFrame#HRule`` — see analyze_tab_form._section / _build_log)."""
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


def _is_dataset(root: Path) -> str:
    """Return "" if *root* looks like a dataset, else a short reason."""
    if not (root / "in").is_dir() or not (root / "out").is_dir():
        return "Needs an 'in' and an 'out' folder."
    return ""


def _is_destination(root: Path) -> str:
    """Return "" if *root* is usable as a destination, else a short reason.

    Only the checks that don't need the other two fields — the "must differ
    from both datasets" rule is cross-field and handled separately by
    :meth:`ConcatenatePage._revalidate_cross`.
    """
    dest = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    if destination_is_complete_dataset(dest):
        return "Already holds a finished dataset — choose an empty folder."
    if destination_has_unrecognized_content(dest) and not destination_has_marker(dest):
        return "Already has unrelated files — choose an empty folder."
    return ""


class ConcatenatePage(QWidget):
    """The Concatenate Datasets feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInReviewRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._running = False
        self._build_ui()

    def _build_ui(self) -> None:
        # Wrap in a scroll area so the page's content (fields, plate
        # selector, dashboard, log) can exceed the window's default height
        # (1640x980 — allwell.py) without crushing whatever's at the bottom —
        # that used to make ConcatDashboard._plate render illegibly small,
        # since its cell size derives from its actual allotted width/height
        # (well_plate_selector's _metrics()). Mirrors the pattern
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

        heading = QLabel("Concatenate datasets", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Append dataset 2 onto dataset 1. The two datasets should be the "
            "same plate imaged in separate sessions; dataset 2's timepoints are "
            "relabelled to continue after dataset 1's last timepoint. The merged "
            "result is written to the destination folder — both originals are "
            "left untouched.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._build_equation_row(outer)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Concatenate", self)
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

        self._open_btn = QPushButton("Open merged dataset in Review", self)
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

    def _build_equation_row(self, outer: QVBoxLayout) -> None:
        row = QWidget(self)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.sm)

        self._ds1_field = PathField(
            "Dataset 1 (base)", pick=folder_picker("Select dataset 1 folder"),
            validator=_is_dataset)
        self._ds2_field = PathField(
            "Dataset 2 (appended)", pick=folder_picker("Select dataset 2 folder"),
            validator=_is_dataset)
        self._dest_field = PathField(
            "Destination", pick=folder_picker("Select destination folder"),
            validator=_is_destination)

        def _operator(text: str) -> QLabel:
            lbl = QLabel(text, row)
            lbl.setObjectName("Heading")
            lbl.setContentsMargins(0, theme.Spacing.lg, 0, 0)
            lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            lbl.setFixedWidth(20)
            return lbl

        hl.addWidget(self._ds1_field, 1)
        hl.addWidget(_operator("+"))
        hl.addWidget(self._ds2_field, 1)
        hl.addWidget(_operator("="))
        hl.addWidget(self._dest_field, 1)
        outer.addWidget(row)

        self._cross_hint = QLabel("", self)
        self._cross_hint.setObjectName("Danger")
        self._cross_hint.setWordWrap(True)
        self._cross_hint.setVisible(False)
        outer.addWidget(self._cross_hint)

        for field in (self._ds1_field, self._ds2_field, self._dest_field):
            field.changed.connect(self._revalidate_cross)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Merge Output", hdr))
        hl.addStretch(1)
        # The dashboard below IS the summary view; the verbose text log is off
        # by default and revealed on demand, matching Analyze's own pattern
        # (analyze_tab_form.py::_build_log / _on_toggle_raw_log).
        self._raw_log_cb = QCheckBox("Show raw log", hdr)
        self._raw_log_cb.setToolTip("Show the full per-file merge log beneath the progress dashboard")
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        hl.addWidget(self._raw_log_cb)
        clear_btn = QPushButton("Clear", hdr)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self.clear_log)
        hl.addWidget(clear_btn)
        outer.addWidget(hdr)
        outer.addWidget(_hrule(self))

        self.dashboard = ConcatDashboard(self)
        # Hidden until the two source datasets are both valid — the plate
        # used to be visible from the moment the page loaded, painted with
        # no wells at all (set_wells() is only ever called once a run's
        # worker reports them), reading as "something is wrong" rather than
        # "nothing has happened yet". _update_status_preview() reveals it
        # once there's a real answer to show, seeded with the wells the run
        # would actually touch.
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)

        self._status = QPlainTextEdit(self)
        self._status.setObjectName("ConcatStatus")
        self._status.setReadOnly(True)
        # Cap the document so a long-running merge can't grow it unbounded —
        # matches Analyze's raw log (analyze_tab_form.py:1241).
        self._status.document().setMaximumBlockCount(10000)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText("Status will appear here.")
        outer.addWidget(self._status, 1)

        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        """Show/hide the verbose text log. The progress dashboard stays
        visible either way; this only toggles the raw per-file stream."""
        for w in (getattr(self, "_status", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))

    # ── public API used by the controller ────────────────────────────────────
    def dataset1_path(self) -> str:
        return self._ds1_field.path()

    def dataset2_path(self) -> str:
        return self._ds2_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def set_dataset1_path(self, path: str) -> None:
        self._ds1_field.set_path(path)

    def set_dataset2_path(self, path: str) -> None:
        self._ds2_field.set_path(path)

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def revalidate_fields(self) -> None:
        """Re-run every field's validator against current disk state.

        A run changes what's on disk under the destination path (and the
        button's enabled state already reflects that on the next check), so
        call this once a run finishes to refresh the inline hints too —
        otherwise a destination that just became "already holds a finished
        dataset" silently disables Concatenate without saying why.
        """
        for field in (self._ds1_field, self._ds2_field, self._dest_field):
            field.revalidate()
        self._revalidate_cross()

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def set_running(self, running: bool) -> None:
        self._running = running
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        self._run_btn.setText("Concatenating…" if running else "▶  Concatenate")
        if running:
            self._open_btn.setVisible(False)

    def show_open_in_review(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(True)

    # ── internals ────────────────────────────────────────────────────────────
    def _inputs_ready(self) -> bool:
        return (
            self._ds1_field.is_valid()
            and self._ds2_field.is_valid()
            and self._dest_field.is_valid()
            and not self._cross_message()
        )

    def _cross_message(self) -> str:
        d1, d2, dest = self._ds1_field.path(), self._ds2_field.path(), self._dest_field.path()
        if dest and (dest == d1 or dest == d2):
            return "Destination must be a different folder from both datasets."
        return ""

    def _revalidate_cross(self) -> None:
        message = self._cross_message()
        self._cross_hint.setText(message)
        self._cross_hint.setVisible(bool(message))
        self._refresh_run_enabled()
        self._update_status_preview()

    def _update_status_preview(self) -> None:
        # Never touches the dashboard once a run has actually started — a
        # run's own worker (ConcatenateController) drives set_wells()/
        # set_phase()/well colouring from that point on, and both source
        # fields should already be locked for the run's duration; this only
        # ever runs from a field's own changed() signal, so in practice a
        # running worker's own dashboard state is never at risk here, but the
        # guard costs nothing and keeps the two responsibilities visibly
        # separate.
        if self._running:
            return
        if not (self._ds1_field.is_valid() and self._ds2_field.is_valid()):
            self.dashboard.setVisible(False)
            self.dashboard.reset()
            return
        wells = sorted(set(preview_wells(self.dataset1_path())) | set(preview_wells(self.dataset2_path())))
        self.dashboard.reset()
        self.dashboard.set_wells(wells)
        self.dashboard.set_headline(f"Ready — {len(wells)} well(s) to merge.")
        self.dashboard.setVisible(True)

    def _refresh_run_enabled(self) -> None:
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())
