"""Qt builder for the Merge Plates page.

An equation-style dataset 1 + dataset 2 = destination row (mirroring
Concatenate's), a block-placement control for remapping dataset 2's wells
onto free positions, and the usual Analyze-style status view (a live per-well
:class:`~datasets.merge_plates.dashboard.MergePlatesDashboard`, raw log
demoted to a collapsible checkbox).

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``); the worker wiring lives in
:mod:`~datasets.merge_plates.controller`.
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
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from util.well_token import canonical_well_label
from widgets.path_field import PathField, folder_picker
from datasets.merge_plates.dashboard import MergePlatesDashboard
from datasets.merge_plates.merge_plates_compute import (
    DatasetPaths,
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
    dest = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    if destination_is_complete_dataset(dest):
        return "Already holds a finished dataset — choose an empty folder."
    if destination_has_unrecognized_content(dest):
        return "Already has unrelated files — choose an empty folder."
    return ""


class MergePlatesPage(QWidget):
    """The Merge Plates feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInReviewRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
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

        heading = QLabel("Merge Plates", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Join two DIFFERENT plates along the well axis into one dataset "
            "— the well-axis sibling of Concatenate, which joins the SAME "
            "plate across time. A well present in both datasets is treated "
            "as two different samples and refused, not stitched: remap "
            "dataset 2's wells onto free positions first if they overlap.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._build_equation_row(outer)
        self._build_remap_row(outer)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Merge", self)
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
            "Dataset 1", pick=folder_picker("Select dataset 1 folder"), validator=_is_dataset)
        self._ds2_field = PathField(
            "Dataset 2", pick=folder_picker("Select dataset 2 folder"), validator=_is_dataset)
        self._dest_field = PathField(
            "Destination", pick=folder_picker("Select destination folder"), validator=_is_destination)

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
            field.changed.connect(self._refresh_run_enabled)

    def _build_remap_row(self, outer: QVBoxLayout) -> None:
        row = QWidget(self)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.sm)
        self._remap_cb = QCheckBox("Place dataset 2 starting at well:", row)
        self._remap_cb.toggled.connect(self._refresh_run_enabled)
        hl.addWidget(self._remap_cb)
        self._remap_origin_edit = QLineEdit("E01", row)
        self._remap_origin_edit.setFixedWidth(60)
        self._remap_origin_edit.setEnabled(False)
        self._remap_cb.toggled.connect(self._remap_origin_edit.setEnabled)
        self._remap_origin_edit.textChanged.connect(self._refresh_run_enabled)
        hl.addWidget(self._remap_origin_edit)
        hl.addStretch(1)
        outer.addWidget(row)

        self._remap_hint = QLabel("", self)
        self._remap_hint.setObjectName("Danger")
        self._remap_hint.setWordWrap(True)
        self._remap_hint.setVisible(False)
        outer.addWidget(self._remap_hint)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Merge Output", hdr))
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

        self.dashboard = MergePlatesDashboard(self)
        # Not shown until a run actually starts — an idle status strip above
        # an empty log reads as "something is wrong" rather than "nothing
        # has happened yet". MergePlatesController reveals it in _on_run().
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)
        self._status = QPlainTextEdit(self)
        self._status.setObjectName("MergePlatesStatus")
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
    def dataset1_path(self) -> str:
        return self._ds1_field.path()

    def dataset2_path(self) -> str:
        return self._ds2_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def remap_enabled(self) -> bool:
        return self._remap_cb.isChecked()

    def remap_origin(self) -> str:
        return self._remap_origin_edit.text().strip()

    def set_dataset1_path(self, path: str) -> None:
        self._ds1_field.set_path(path)

    def set_dataset2_path(self, path: str) -> None:
        self._ds2_field.set_path(path)

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def set_remap_enabled(self, enabled: bool) -> None:
        self._remap_cb.setChecked(bool(enabled))

    def set_remap_origin(self, origin: str) -> None:
        self._remap_origin_edit.setText(origin or "")

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        self._run_btn.setText("Merging…" if running else "▶  Merge")
        if running:
            self._open_btn.setVisible(False)

    def show_open_in_review(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(True)

    def revalidate_fields(self) -> None:
        for field in (self._ds1_field, self._ds2_field, self._dest_field):
            field.revalidate()
        self._refresh_run_enabled()

    # ── internals ────────────────────────────────────────────────────────────
    def _cross_message(self) -> str:
        d1, d2, dest = self.dataset1_path(), self.dataset2_path(), self.destination_path()
        if dest and (dest == d1 or dest == d2):
            return "Destination must be a different folder from both datasets."
        if d1 and d1 == d2:
            return "Dataset 1 and Dataset 2 must be different folders."
        return ""

    def _remap_message(self) -> str:
        if not self.remap_enabled():
            return ""
        if not canonical_well_label(self.remap_origin()):
            return f"{self.remap_origin()!r} isn't a valid 96-well position."
        return ""

    def _inputs_ready(self) -> bool:
        return (
            self._ds1_field.is_valid()
            and self._ds2_field.is_valid()
            and self._dest_field.is_valid()
            and not self._cross_message()
            and not self._remap_message()
        )

    def _refresh_run_enabled(self) -> None:
        cross = self._cross_message()
        self._cross_hint.setText(cross)
        self._cross_hint.setVisible(bool(cross))
        remap_msg = self._remap_message()
        self._remap_hint.setText(remap_msg)
        self._remap_hint.setVisible(bool(remap_msg))
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())
