"""Qt builder for the Recipes page.

Recipes are plans; the Job Queue holds the jobs a plan becomes. This page is
where the plans live — the saved recipes, each one's steps and what they do,
renaming, copying, sharing and deleting them — and where a plan's runs are
read afterwards: every application of the recipe, how far it got, and its log.

Pure view: it owns widgets and emits intent; ``recipes_controller`` wires it
to :class:`~datasets.job_queue.recipe_actions.RecipeActions` and the queue.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from datasets.core.log_panel import hrule as _hrule
from widgets.flow_layout import FlowLayout

#: How a templated path reads on this page: what it stands for, not the token.
_PLACEHOLDER_WORDS = (("{source_parent}", "‹input's folder›"),
                      ("{source_name}", "‹input name›"),
                      ("{source}", "‹input›"))


def readable(text: str) -> str:
    """*text* with the recipe's placeholders written as words."""
    for token, words in _PLACEHOLDER_WORDS:
        text = text.replace(token, words)
    return text


class RecipesPage(QWidget):
    """The Recipes feature body."""

    recipeSelected = Signal(str)
    runSelected = Signal(str)
    applyRequested = Signal(str)
    renameRequested = Signal(str)
    duplicateRequested = Signal(str)
    exportRequested = Signal(str)
    deleteRequested = Signal(str)
    importRequested = Signal()
    showQueueRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current = ""
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

        heading = QLabel("Recipes", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "A recipe is a chain of jobs saved as a plan — Watch, Convert, "
            "Analyze, say — that you apply to each new acquisition. Applying one "
            "adds its jobs to the Job Queue. Save a recipe from any queued job's "
            "⋯ menu: “Save chain as recipe…”.", self)
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        top = QWidget(self)
        tl = FlowLayout(top, h_spacing=theme.Spacing.sm, v_spacing=theme.Spacing.xs)
        self._import_btn = QPushButton("Import…", self)
        self._import_btn.setProperty("variant", "secondary")
        self._import_btn.setToolTip("Add a recipe exported from All-Well, here or elsewhere.")
        self._import_btn.clicked.connect(self.importRequested)
        tl.addWidget(self._import_btn)
        self._queue_btn = QPushButton("Job Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Go to the Job Queue, where applied recipes run.")
        self._queue_btn.clicked.connect(self.showQueueRequested)
        tl.addWidget(self._queue_btn)
        outer.addWidget(top)

        self._empty = QLabel(
            "No recipes yet. Set a chain up in the Job Queue, then choose "
            "“Save chain as recipe…” from one of its jobs' ⋯ menu.", self)
        self._empty.setObjectName("Muted")
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        self._list = QListWidget(self)
        self._list.setObjectName("RecipeList")
        self._list.setMaximumHeight(theme.Spacing.lg * 10)
        self._list.currentItemChanged.connect(self._on_current_changed)
        outer.addWidget(self._list)

        self._detail = QWidget(self)
        dl = QVBoxLayout(self._detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(theme.Spacing.sm)
        self._name = QLabel("", self._detail)
        self._name.setObjectName("SectionTitle")
        self._name.setWordWrap(True)
        dl.addWidget(self._name)
        self._origin = QLabel("", self._detail)
        self._origin.setObjectName("Muted")
        self._origin.setWordWrap(True)
        dl.addWidget(self._origin)

        actions = QWidget(self._detail)
        al = FlowLayout(actions, h_spacing=theme.Spacing.sm, v_spacing=theme.Spacing.xs)
        self._buttons: dict[str, QPushButton] = {}
        for key, text, variant, signal in (
                ("apply", "▶  Apply to…", "primary", self.applyRequested),
                ("rename", "Rename…", "secondary", self.renameRequested),
                ("duplicate", "Duplicate", "secondary", self.duplicateRequested),
                ("export", "Export…", "secondary", self.exportRequested),
                ("delete", "Delete…", "danger", self.deleteRequested)):
            button = QPushButton(text, actions)
            button.setProperty("variant", variant)
            button.clicked.connect(lambda _checked=False, s=signal: s.emit(self._current))
            al.addWidget(button)
            self._buttons[key] = button
        dl.addWidget(actions)

        steps_title = QLabel("Steps", self._detail)
        steps_title.setObjectName("SectionTitle")
        dl.addWidget(steps_title)
        self._steps = QLabel("", self._detail)
        self._steps.setObjectName("RecipeSteps")
        self._steps.setWordWrap(True)
        self._steps.setTextFormat(Qt.PlainText)
        self._steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self._steps)
        self._notes = QLabel("", self._detail)
        self._notes.setObjectName("Muted")
        self._notes.setWordWrap(True)
        dl.addWidget(self._notes)

        dl.addWidget(_hrule(self._detail))
        runs_title = QLabel("Runs", self._detail)
        runs_title.setObjectName("SectionTitle")
        dl.addWidget(runs_title)
        self._no_runs = QLabel("Not applied yet.", self._detail)
        self._no_runs.setObjectName("Muted")
        dl.addWidget(self._no_runs)
        self._runs = QListWidget(self._detail)
        self._runs.setObjectName("RecipeRuns")
        self._runs.setMaximumHeight(theme.Spacing.lg * 8)
        self._runs.currentItemChanged.connect(self._on_run_changed)
        dl.addWidget(self._runs)
        self._log = QPlainTextEdit(self._detail)
        self._log.setObjectName("RecipeRunLog")
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        dl.addWidget(self._log, 1)
        outer.addWidget(self._detail)
        outer.addStretch(1)

    # ── public API used by the controller ──────────────────────────────
    def set_recipes(self, recipes: list[tuple[str, str]], select: str = "") -> None:
        """Show (name, chain) per recipe and select *select* (or keep the
        current one, or the first)."""
        wanted = select or self._current
        self._list.blockSignals(True)
        self._list.clear()
        for name, summary in recipes:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            item.setToolTip(summary)
            self._list.addItem(item)
        self._list.blockSignals(False)
        names = [name for name, _summary in recipes]
        self._empty.setVisible(not names)
        self._list.setVisible(bool(names))
        self._detail.setVisible(bool(names))
        target = wanted if wanted in names else (names[0] if names else "")
        if target:
            self._list.setCurrentRow(names.index(target))
            self._on_current_changed(self._list.currentItem(), None)
        else:
            self._current = ""

    def current_recipe(self) -> str:
        return self._current

    def show_recipe(self, name: str, origin: str, steps: list[str], notes: list[str]) -> None:
        self._name.setText(name)
        self._origin.setText(origin)
        self._steps.setText("\n".join(f"{i + 1}. {line}" for i, line in enumerate(steps)))
        self._notes.setText(" ".join(notes))
        self._notes.setVisible(bool(notes))

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
        self._no_runs.setVisible(not ids)
        self._runs.setVisible(bool(ids))
        if keep in ids:
            self._runs.setCurrentRow(ids.index(keep))
        else:
            self._log.setVisible(False)

    def current_run(self) -> str:
        item = self._runs.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ""

    def show_log(self, text: str) -> None:
        bar = self._log.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        self._log.setPlainText(text)
        self._log.setVisible(bool(text))
        if at_end:
            bar.setValue(bar.maximum())

    def log_text(self) -> str:
        return self._log.toPlainText()

    def steps_text(self) -> str:
        return self._steps.text()

    def button(self, key: str) -> QPushButton:
        return self._buttons[key]

    # ── internals ────────────────────────────────────────────────────────────
    def _on_current_changed(self, current, _previous) -> None:
        self._current = str(current.data(Qt.UserRole)) if current is not None else ""
        if self._current:
            self.recipeSelected.emit(self._current)

    def _on_run_changed(self, current, _previous) -> None:
        if current is not None:
            self.runSelected.emit(str(current.data(Qt.UserRole)))
