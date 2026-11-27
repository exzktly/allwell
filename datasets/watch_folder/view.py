"""Qt builder for the Watch folder page.

A folder picker, the quiet period, whether the folder must change before the
countdown starts, an optional give-up time, a **Start watching** / **+ Queue**
row and a one-line live status — the countdown a person checks on — above the
usual raw log.

Pure view: it owns widgets and emits intent (``runRequested``,
``queueRequested``); the worker wiring lives in
:mod:`~datasets.watch_folder.controller`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from datasets.watch_folder.watch_folder_compute import DEFAULT_QUIET_MINUTES, WatchStatus
from widgets.path_field import PathField, folder_picker
from widgets.spin_box import DoubleSpinBox

#: Shown under the folder field when the folder is not there yet. Muted, not
#: red: watching a folder the acquisition software will create is a normal
#: thing to set up.
NOT_YET_NOTE = "This folder does not exist yet — the watch waits for it to appear."


def _folder_problem(path: Path) -> str:
    """A red hint only for a path that can never be watched."""
    if path.exists() and not path.is_dir():
        return "This is a file, not a folder."
    return ""


class WatchFolderPage(LogPanelMixin, QWidget):
    """The Watch folder feature body."""

    runRequested = Signal()
    queueRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._build_ui()

    def _build_ui(self) -> None:
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

        heading = QLabel("Watch Folder", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Wait for a folder to finish receiving files, then let the jobs "
            "queued after this one run. Put it first in a chain — Watch, then "
            "Convert, then Analyze — so an acquisition is processed as soon as "
            "the microscope stops writing it.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._folder_field = PathField(
            "Folder to watch", pick=folder_picker("Select a folder to watch"),
            validator=_folder_problem)
        self._folder_field.changed.connect(self._on_inputs_changed)
        outer.addWidget(self._folder_field)

        quiet_row = QWidget(self)
        ql = QHBoxLayout(quiet_row)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(theme.Spacing.sm)
        ql.addWidget(QLabel("Settled after no change for (minutes)", quiet_row))
        self._quiet_spin = DoubleSpinBox(quiet_row)
        self._quiet_spin.setObjectName("WatchQuietMinutes")
        self._quiet_spin.setDecimals(1)
        self._quiet_spin.setRange(0.1, 24 * 60.0)
        self._quiet_spin.setSingleStep(1.0)
        self._quiet_spin.setValue(DEFAULT_QUIET_MINUTES)
        self._quiet_spin.setToolTip(
            "How long the folder must go with no file added, removed, growing "
            "or being modified before the watch counts it as finished. Make it "
            "longer than the longest pause the acquisition takes between "
            "writes — between timepoints, for example.")
        ql.addWidget(self._quiet_spin)
        ql.addStretch(1)
        outer.addWidget(quiet_row)

        self._require_change_cb = QCheckBox(
            "Wait for new files before counting", self)
        self._require_change_cb.setChecked(True)
        self._require_change_cb.setToolTip(
            "On: the countdown starts only once something in the folder "
            "changes, so a watch set up before an acquisition does not fire on "
            "what is already there. Off: a folder that is already complete "
            "settles after one quiet period.")
        outer.addWidget(self._require_change_cb)

        give_up_row = QWidget(self)
        gl = QHBoxLayout(give_up_row)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(theme.Spacing.sm)
        gl.addWidget(QLabel("Give up after (hours, 0 = never)", give_up_row))
        self._give_up_spin = DoubleSpinBox(give_up_row)
        self._give_up_spin.setObjectName("WatchGiveUpHours")
        self._give_up_spin.setDecimals(1)
        self._give_up_spin.setRange(0.0, 24 * 14.0)
        self._give_up_spin.setSingleStep(1.0)
        self._give_up_spin.setToolTip(
            "Fail the watch, and block the jobs after it, if the folder has not "
            "settled in this many hours. 0 waits indefinitely.")
        gl.addWidget(self._give_up_spin)
        gl.addStretch(1)
        outer.addWidget(give_up_row)

        btn_row = QWidget(self)
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Start watching", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        bl.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "default")
        self._queue_btn.setToolTip(
            "Add this watch to the Job Queue, to run ahead of the jobs that "
            "should wait for the folder.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        bl.addWidget(self._queue_btn)
        bl.addStretch(1)
        outer.addWidget(btn_row)

        self.build_log_header(outer, "Status")
        self._status_label = QLabel("", self)
        self._status_label.setObjectName("WatchStatus")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        outer.addWidget(self._status_label)
        self.build_log_tail(outer, placeholder="Log will appear here.")
        outer.addStretch(1)

    # ── public API used by the controller ──────────────────────────────
    def folder_path(self) -> str:
        return self._folder_field.path()

    def set_folder_path(self, path: str) -> None:
        self._folder_field.set_path(path)
        self._on_inputs_changed()

    def quiet_minutes(self) -> float:
        return float(self._quiet_spin.value())

    def set_quiet_minutes(self, minutes: float) -> None:
        self._quiet_spin.setValue(float(minutes))

    def require_change(self) -> bool:
        return self._require_change_cb.isChecked()

    def set_require_change(self, on: bool) -> None:
        self._require_change_cb.setChecked(bool(on))

    def give_up_hours(self) -> float:
        return float(self._give_up_spin.value())

    def set_give_up_hours(self, hours: float) -> None:
        self._give_up_spin.setValue(float(hours))

    def show_status(self, status: WatchStatus) -> None:
        self.set_status_text(status.sentence())

    def set_status_text(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))

    def status_text(self) -> str:
        return self._status_label.text()

    def set_running(self, running: bool) -> None:
        self._running = running
        self._run_btn.setText("Watching…" if running else "▶  Start watching")
        self._folder_field.setLocked(running)
        for widget in (self._quiet_spin, self._require_change_cb, self._give_up_spin):
            widget.setEnabled(not running)
        self._on_inputs_changed()

    # ── internals ────────────────────────────────────────────────────────────
    def _inputs_ready(self) -> bool:
        return self._folder_field.is_valid()

    def _on_inputs_changed(self) -> None:
        ready = self._inputs_ready()
        self._run_btn.setEnabled(ready and not self._running)
        self._queue_btn.setEnabled(ready and not self._running)
        path = self.folder_path()
        if path and ready and not Path(path).exists():
            self._folder_field.show_note(NOT_YET_NOTE)
