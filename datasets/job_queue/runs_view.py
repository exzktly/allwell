"""Qt builder for the Runs tab of Automation.

A run is one use of a recipe: the jobs one **Apply** queued, and the journal
they wrote. The queue forgets a job once it is cleared; this tab does not.
Every run of every recipe, newest first, filterable by recipe, each with how
far it got, and the selected run's log.

Pure view; ``recipes_controller`` fills it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from datasets.core.log_panel import hrule as _hrule

#: The filter entry that shows every recipe's runs.
ALL_RECIPES = "All recipes"

#: The run list's ceiling, so the log below it stays on screen.
_RUN_LIST_MAX_H = 200


class RunsPage(QWidget):
    """Every recipe run, and the selected one's log."""

    runSelected = Signal(str)
    filterChanged = Signal(str)

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

        heading = QLabel("Runs", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))
        blurb = QLabel(
            "Every time a recipe was applied: when, to what, how far it got, and "
            "its log — which step started on which folder, which file a "
            "conversion chose, what each step wrote, and any pause, retry or "
            "restart. A run stays here after its jobs are cleared from the queue.",
            self)
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        row = QWidget(self)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(theme.Spacing.sm)
        rl.addWidget(QLabel("Recipe", row))
        self._filter = QComboBox(row)
        self._filter.setObjectName("RunsRecipeFilter")
        self._filter.currentTextChanged.connect(self.filterChanged)
        rl.addWidget(self._filter, 1)
        outer.addWidget(row)

        self._empty = QLabel("No recipe has been applied yet.", self)
        self._empty.setObjectName("Muted")
        outer.addWidget(self._empty)
        self._runs = QListWidget(self)
        self._runs.setObjectName("RecipeRuns")
        self._runs.setMaximumHeight(_RUN_LIST_MAX_H)
        self._runs.currentItemChanged.connect(self._on_run_changed)
        outer.addWidget(self._runs)
        self._log = QPlainTextEdit(self)
        self._log.setObjectName("RecipeRunLog")
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        outer.addWidget(self._log, 1)

    # ── public API used by the controller ──────────────────────────────
    def set_filters(self, names: list[str], select: str = "") -> None:
        """The recipes to filter by; *select* (or the current filter) stays."""
        keep = select or self.current_filter()
        self._filter.blockSignals(True)
        self._filter.clear()
        self._filter.addItems([ALL_RECIPES, *names])
        index = self._filter.findText(keep)
        self._filter.setCurrentIndex(index if index >= 0 else 0)
        self._filter.blockSignals(False)

    def current_filter(self) -> str:
        return self._filter.currentText() or ALL_RECIPES

    def set_filter(self, name: str) -> None:
        index = self._filter.findText(name)
        self._filter.setCurrentIndex(index if index >= 0 else 0)

    def set_runs(self, runs: list[tuple[str, str]], select: str = "") -> None:
        """Show (run id, one line) per run, newest first."""
        keep = select or self.current_run()
        self._runs.blockSignals(True)
        self._runs.clear()
        for run_id, line in runs:
            item = QListWidgetItem(line)
            item.setData(Qt.UserRole, run_id)
            self._runs.addItem(item)
        self._runs.blockSignals(False)
        ids = [run_id for run_id, _line in runs]
        self._empty.setVisible(not ids)
        self._runs.setVisible(bool(ids))
        if keep in ids:
            self._runs.setCurrentRow(ids.index(keep))
        else:
            self._log.setVisible(False)

    def current_run(self) -> str:
        item = self._runs.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ""

    def run_lines(self) -> list[str]:
        return [self._runs.item(i).text() for i in range(self._runs.count())]

    def show_log(self, text: str) -> None:
        bar = self._log.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        self._log.setPlainText(text)
        self._log.setVisible(bool(text))
        if at_end:
            bar.setValue(bar.maximum())

    def log_text(self) -> str:
        return self._log.toPlainText()

    def _on_run_changed(self, current, _previous) -> None:
        if current is not None:
            self.runSelected.emit(str(current.data(Qt.UserRole)))
