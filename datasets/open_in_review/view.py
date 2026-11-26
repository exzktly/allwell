"""Qt builder for the Open in Review page.

A dataset field, **▶ Open in Review** / **+ Queue**, and a one-line outcome
above the shared raw log. Queued, it is the step at the end of a chain that
puts the finished analysis in front of the user; the controller says when.

Pure view: it owns widgets and emits intent (``runRequested``,
``queueRequested``).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
from datasets.core.pending_inputs import RUN_WAITS, any_pending, apply_pending
from datasets.open_in_review.open_in_review_compute import not_reviewable_reason, reviewable_root
from widgets.path_field import PathField, folder_picker


def _dataset_problem(path: Path) -> str:
    """A red hint for a folder that exists and holds nothing Review can show."""
    if reviewable_root(path) is not None:
        return ""
    return not_reviewable_reason(path)


class OpenInReviewPage(LogPanelMixin, QWidget):
    """The Open in Review feature body."""

    runRequested = Signal()
    queueRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

        heading = QLabel("Open in Review", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Show an analysed dataset in Review. Queued after Analyze — as the "
            "last step of a chain or a recipe — it loads the result as soon as "
            "it is ready, and moves the window to Review only if nobody is using "
            "the app at that moment.", self)
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._dataset_field = PathField(
            "Dataset folder", pick=folder_picker("Select an analysed dataset"),
            validator=_dataset_problem)
        self._dataset_field.changed.connect(self._on_inputs_changed)
        outer.addWidget(self._dataset_field)

        btn_row = QWidget(self)
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Open in Review", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        bl.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip(
            "Add this to the Job Queue, after the job that writes the dataset.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        bl.addWidget(self._queue_btn)
        bl.addStretch(1)
        outer.addWidget(btn_row)

        self.build_log_header(outer, "Status")
        self._status_label = QLabel("", self)
        self._status_label.setObjectName("OpenInReviewStatus")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        outer.addWidget(self._status_label)
        self.build_log_tail(outer, placeholder="Log will appear here.")
        outer.addStretch(1)

    # ── public API used by the controller ──────────────────────────────
    def dataset_path(self) -> str:
        return self._dataset_field.path()

    def set_dataset_path(self, path: str) -> None:
        self._dataset_field.set_path(path)
        self._on_inputs_changed()

    def set_status_text(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(bool(text))

    def status_text(self) -> str:
        return self._status_label.text()

    def input_fields(self) -> dict:
        """The field an earlier queued job's output can fill."""
        return {"dataset": self._dataset_field}

    def set_pending_inputs(self, pending: dict) -> None:
        apply_pending({"dataset": self._dataset_field}, pending)
        self._on_inputs_changed()

    def has_pending_inputs(self) -> bool:
        return any_pending({"dataset": self._dataset_field})

    # ── internals ────────────────────────────────────────────────────────────
    def _on_inputs_changed(self) -> None:
        ready = self._dataset_field.is_valid()
        pending = self.has_pending_inputs()
        self._run_btn.setEnabled(ready and not pending)
        self._run_btn.setToolTip(RUN_WAITS if pending else "")
        self._queue_btn.setEnabled(ready)
