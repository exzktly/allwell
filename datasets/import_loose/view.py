"""Qt builder for the Import Loose Folder page.

A source-folder picker, editable filename-schema/separator fields, a live
preview readout (wells × FOVs × timepoints × channels, plus unparsed/
unreadable counts) that updates on every keystroke, a destination-folder
picker (hidden when "group in place" is checked), and the usual
Analyze-style status section — a per-well plate map
(:class:`~datasets.import_loose.dashboard.ImportLooseDashboard`) with the raw
log demoted to a collapsible checkbox, the same shape
``datasets/concatenate/view.py`` and ``datasets/subset/view.py`` use.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInAnalyzeRequested``); the worker wiring lives in
:mod:`~datasets.import_loose.controller`.
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
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP
from widgets._support import set_variant
from widgets.path_field import PathField, folder_picker
from datasets._common.dataset_paths import DatasetPaths, destination_has_content
from datasets.import_loose.dashboard import ImportLooseDashboard
from datasets.import_loose.import_loose_compute import scan_folder


def _is_destination(root: Path) -> str:
    """Return "" if *root* is usable as a new-destination target, else a
    short reason — the page-side early warning for the same guard
    ``apply.py::_check_new_destination`` enforces authoritatively before any
    write happens."""
    dest = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    if destination_has_content(dest):
        return "Already has files under 'in' or 'out' — choose an empty folder."
    return ""


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


class ImportLoosePage(QWidget):
    """The Import Loose Folder feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInAnalyzeRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._build_ui()
        self.refresh_readout()

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

        heading = QLabel("Import Loose Folder", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Group a folder of loose, ungrouped TIFs into per-well "
            "in/<WELL>.zip archives — the layout Analyze reads directly. "
            "Writes to a new destination by default; check the box below to "
            "group in place instead (deletes the loose TIFs on success, the "
            "same way Analyze's own loose-file handling does).",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField(
            "Loose-TIF folder", pick=folder_picker("Select folder of loose TIFs"))
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)

        schema_row = QWidget(self)
        sl = QHBoxLayout(schema_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(theme.Spacing.sm)
        sl.addWidget(QLabel("Schema:", schema_row))
        self._schema_edit = QLineEdit(DEFAULT_SCHEMA, schema_row)
        self._schema_edit.textChanged.connect(self._on_readout_input_changed)
        sl.addWidget(self._schema_edit, 1)
        sl.addWidget(QLabel("Separator:", schema_row))
        self._sep_edit = QLineEdit(DEFAULT_SEP, schema_row)
        self._sep_edit.setFixedWidth(40)
        self._sep_edit.textChanged.connect(self._on_readout_input_changed)
        sl.addWidget(self._sep_edit)
        outer.addWidget(schema_row)

        self._readout = QLabel("", self)
        self._readout.setObjectName("Muted")
        self._readout.setWordWrap(True)
        outer.addWidget(self._readout)

        self._in_place_cb = QCheckBox(
            "Group in place (delete loose TIFs on success)", self)
        self._in_place_cb.toggled.connect(self._on_in_place_toggled)
        outer.addWidget(self._in_place_cb)

        self._dest_field = PathField(
            "Destination folder", pick=folder_picker("Select destination folder"),
            validator=_is_destination)
        self._dest_field.changed.connect(self._refresh_run_enabled)
        outer.addWidget(self._dest_field)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Convert", self)
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

        self._open_btn = QPushButton("Open dataset in Analyze", self)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(lambda: self.openInAnalyzeRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)

        # A trailing stretch outranks every 0-stretch item above it for any
        # leftover space the QScrollArea's content widget picks up filling
        # the viewport (see widgets/plate_progress.py's own Maximum policy
        # fix for why the dashboard itself no longer eats that space) — so
        # slack collects as blank page background below everything instead
        # of inflating a label, field, or the dashboard's own plate map.
        outer.addStretch(1)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Conversion Output", hdr))
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

        self.dashboard = ImportLooseDashboard(self)
        # Hidden until the source folder scans to at least a real answer —
        # _update_status_preview() reveals it seeded with the wells the scan
        # actually found, and ImportLooseController takes over colouring it
        # once a run actually starts.
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)
        self._status = QPlainTextEdit(self)
        self._status.setObjectName("ImportLooseStatus")
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

    def filename_schema(self) -> str:
        return self._schema_edit.text().strip() or DEFAULT_SCHEMA

    def filename_sep(self) -> str:
        return self._sep_edit.text() or DEFAULT_SEP

    def in_place(self) -> bool:
        return self._in_place_cb.isChecked()

    def set_source_path(self, path: str) -> None:
        self._src_field.set_path(path)

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def set_in_place(self, in_place: bool) -> None:
        self._in_place_cb.setChecked(bool(in_place))

    def set_filename_schema(self, schema: str) -> None:
        self._schema_edit.setText(schema or DEFAULT_SCHEMA)

    def set_filename_sep(self, sep: str) -> None:
        self._sep_edit.setText(sep or DEFAULT_SEP)

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running and self._can_run())
        self._queue_btn.setEnabled(not running and self._can_run())
        self._run_btn.setText("Converting…" if running else "▶  Convert")
        if running:
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")
        # Locked alongside the run (ImportLooseController._launch captures
        # source_path() once at launch and never re-reads it afterward, but
        # the field itself must not drift out from under the user mid-run
        # either — same class of bug as Subset's source/destination fields).
        self._src_field.setEnabled(not running)
        self._dest_field.setEnabled(not running)
        self._schema_edit.setEnabled(not running)
        self._sep_edit.setEnabled(not running)
        self._in_place_cb.setEnabled(not running)

    def show_open_in_analyze(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(bool(dest_root))
        if dest_root:
            # Promoted to the primary action on success, demoting Convert —
            # see ConcatenatePage.show_open_in_review for the reasoning.
            set_variant(self._open_btn, "primary")
            set_variant(self._run_btn, "secondary")

    def revalidate_fields(self, *, ok: bool = False) -> None:
        """Re-run the destination field's validator against current disk
        state, called once a run finishes (mirrors
        ConcatenatePage.revalidate_fields / MergePlatesPage.revalidate_fields).

        Without this, a new-destination run's own success silently disabled
        a second Convert into the same folder — the destination genuinely
        now has content, but nothing had ever re-run the validator to say
        so, so the button just went dark with no visible reason. *ok*: swap
        the validator's alarming "already has files" wording for a neutral
        note, since that's the expected result of the run that just
        succeeded, not a problem with the pick.
        """
        if self.in_place():
            return
        self._dest_field.revalidate()
        if ok and self._dest_field.message():
            self._dest_field.show_note(
                "This destination now holds the converted dataset from "
                "that run — pick a new folder to convert again."
            )
        self._refresh_run_enabled()

    def refresh_readout(self) -> None:
        src = self.source_path()
        if not src or not Path(src).is_dir():
            self._readout.setText("Pick a folder to preview.")
            self._refresh_run_enabled()
            return
        preview = scan_folder(
            src, filename_schema=self.filename_schema(), filename_sep=self.filename_sep(),
        )
        lines = [preview.summary()]
        if preview.unreadable_count:
            lines.append(f"{preview.unreadable_count} unreadable file(s) (broken symlinks) skipped.")
        if preview.unparsed_files:
            lines.append(
                f"{len(preview.unparsed_files)} of {preview.examined} file(s) didn't match the "
                "schema and would be left alone."
            )
        self._readout.setText(" ".join(lines))
        self._refresh_run_enabled()

    # ── internals ────────────────────────────────────────────────────────────
    def _on_readout_input_changed(self, *_args) -> None:
        self.refresh_readout()
        self._update_status_preview()

    def _update_status_preview(self) -> None:
        # Deliberately separate from refresh_readout() -- ImportLooseController
        # ._on_finished calls refresh_readout() right after a run completes
        # (to refresh the readout text/run-gate against any now-changed
        # destination state), and by then the dashboard already holds that
        # run's own final result (set_done()/set_failed(), called before
        # _on_finished). Folding this into refresh_readout() would
        # immediately overwrite that with a plain pending-coloured preview.
        # Wired instead to the specific interactive-change handlers, none of
        # which fire mid-run (every relevant field is locked for the run's
        # duration) or as part of run completion.
        src = self.source_path()
        if not src or not Path(src).is_dir():
            self.dashboard.setVisible(False)
            return
        preview = scan_folder(
            src, filename_schema=self.filename_schema(), filename_sep=self.filename_sep(),
        )
        if not preview.wells:
            # Nothing the current schema/separator recognises yet -- an
            # empty "Ready — 0 well(s)" plate would answer a question
            # ("what will this convert?") with "nothing", which reads the
            # same as "broken" rather than "not ready yet".
            self.dashboard.setVisible(False)
            return
        self.dashboard.reset()
        self.dashboard.set_wells(preview.wells)
        self.dashboard.set_headline(f"Ready — {len(preview.wells)} well(s) to convert.")
        self.dashboard.setVisible(True)

    def _on_source_changed(self) -> None:
        set_variant(self._run_btn, "primary")
        self.refresh_readout()
        self._update_status_preview()

    def _on_in_place_toggled(self, checked: bool) -> None:
        self._dest_field.setVisible(not checked)
        self._refresh_run_enabled()

    def _can_run(self) -> bool:
        src = self.source_path()
        if not src or not Path(src).is_dir():
            return False
        if self.in_place():
            return True
        return bool(self.destination_path()) and self._dest_field.is_valid()

    def _refresh_run_enabled(self) -> None:
        self._run_btn.setEnabled(self._can_run())
        self._queue_btn.setEnabled(self._can_run())
