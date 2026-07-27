"""Qt builder for the Concatenate Datasets page (the main viewer area).

Three folder fields — dataset 1 (the base / earlier timepoints), dataset 2 (the
one appended after), and a fresh destination — over a status window that streams
the worker's progress. The folder-picker rows reuse the Analyze pane's pattern
(read-only ``QLineEdit`` + a "Browse…" button opening
``QFileDialog.getExistingDirectory`` with ``DontResolveSymlinks``; see
``analyze_tab_form._build_folders_section``).

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``); the worker wiring lives in
:mod:`~datasets.concatenate.controller`. Colours/spacing come from the app QSS
and theme tokens — nothing hardcoded (CLAUDE.md §5).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import theme


def _is_dataset(root: Path) -> str:
    """Return "" if *root* looks like a dataset, else a short reason."""
    if not (root / "in").is_dir() or not (root / "out").is_dir():
        return "Needs an 'in' and an 'out' folder."
    return ""


class _FolderField(QWidget):
    """Label + read-only path box + Browse button, with an optional validator."""

    changed = Signal()

    def __init__(
        self,
        title: str,
        *,
        prompt: str,
        validator: Callable[[Path], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._validator = validator
        self._prompt = prompt

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.Spacing.xs)

        label = QLabel(title, self)
        label.setWordWrap(True)
        col.addWidget(label)

        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(theme.Spacing.xs)
        self._edit = QLineEdit(row)
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("(not set)")
        self._edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row_layout.addWidget(self._edit, 1)
        browse = QPushButton("Browse…", row)
        browse.setProperty("variant", "secondary")
        browse.clicked.connect(self._browse)
        row_layout.addWidget(browse)
        col.addWidget(row)

        self._hint = QLabel("", self)
        self._hint.setObjectName("Muted")
        self._hint.setWordWrap(True)
        col.addWidget(self._hint)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            self._prompt,
            self._edit.text() or "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if chosen:
            self._edit.setText(chosen)
            self._revalidate()
            self.changed.emit()

    def _revalidate(self) -> None:
        text = self._edit.text().strip()
        if not text or self._validator is None:
            self._hint.setText("")
            return
        self._hint.setText(self._validator(Path(text)))

    def path(self) -> str:
        return self._edit.text().strip()

    def is_valid(self) -> bool:
        text = self.path()
        if not text:
            return False
        if self._validator is None:
            return True
        return self._validator(Path(text)) == ""


class ConcatenatePage(QWidget):
    """The Concatenate Datasets feature body."""

    runRequested = Signal()
    openInReviewRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Concatenate datasets", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)

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

        self._ds1_field = _FolderField(
            "Dataset 1 (base — earlier timepoints)",
            prompt="Select dataset 1 folder",
            validator=_is_dataset,
        )
        self._ds2_field = _FolderField(
            "Dataset 2 (appended — later timepoints)",
            prompt="Select dataset 2 folder",
            validator=_is_dataset,
        )
        self._dest_field = _FolderField(
            "Destination (a new, empty folder)",
            prompt="Select destination folder",
        )
        for field in (self._ds1_field, self._ds2_field, self._dest_field):
            field.changed.connect(self._refresh_run_enabled)
            outer.addWidget(field)

        self._run_btn = QPushButton("Concatenate", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        outer.addWidget(self._run_btn, 0, Qt.AlignLeft)

        status_label = QLabel("Status", self)
        outer.addWidget(status_label)
        self._status = QPlainTextEdit(self)
        self._status.setObjectName("ConcatStatus")
        self._status.setReadOnly(True)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText("Status will appear here.")
        outer.addWidget(self._status, 1)

        self._open_btn = QPushButton("Open merged dataset in Review", self)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(lambda: self.openInReviewRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)

    # ── public API used by the controller ────────────────────────────────────
    def dataset1_path(self) -> str:
        return self._ds1_field.path()

    def dataset2_path(self) -> str:
        return self._ds2_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._run_btn.setText("Concatenating…" if running else "Concatenate")
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
            and bool(self._dest_field.path())
        )

    def _refresh_run_enabled(self) -> None:
        self._run_btn.setEnabled(self._inputs_ready())
