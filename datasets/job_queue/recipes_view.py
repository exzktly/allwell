"""Qt builder for the Recipes page.

Recipes are plans; the Job Queue holds the jobs a plan becomes. This page is
where the plans live — the saved recipes, each one's steps and what they do,
renaming, copying, sharing and deleting them — and where you go from a plan to
its runs, which the Runs tab beside it lists.

Pure view: it owns widgets and emits intent; ``recipes_controller`` wires it
to :class:`~datasets.job_queue.recipe_actions.RecipeActions` and the queue.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from datasets.core.log_panel import hrule as _hrule
from widgets.flow_layout import FlowLayout

#: The recipe list's ceiling: enough for a handful of rows before it scrolls,
#: so the selected recipe's steps below stay on screen.
_RECIPE_LIST_MAX_H = 160

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
    showRunsRequested = Signal(str)
    #: (recipe name, on) — "open the result in Review" toggled.
    openResultToggled = Signal(str, bool)
    applyRequested = Signal(str)
    renameRequested = Signal(str)
    duplicateRequested = Signal(str)
    exportRequested = Signal(str)
    deleteRequested = Signal(str)
    importRequested = Signal()

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

        heading = QLabel("Recipes", self)  # the Recipes tab of Automation
        heading.setProperty("textRole", "sectionHeading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "A recipe is a chain of jobs saved as a plan — Watch, Convert, "
            "Analyze, say — that you apply to each new acquisition. Applying one "
            "adds its jobs to the Job Queue. Save a recipe from any queued job's "
            "⋯ menu: “Save chain as recipe…”.", self)
        blurb.setProperty("textRole", "help")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        top = QWidget(self)
        tl = FlowLayout(top, h_spacing=theme.Spacing.sm, v_spacing=theme.Spacing.xs)
        self._import_btn = QPushButton("Import…", self)
        self._import_btn.setProperty("variant", "default")
        self._import_btn.setToolTip("Add a recipe exported from All-Well, here or elsewhere.")
        self._import_btn.clicked.connect(self.importRequested)
        tl.addWidget(self._import_btn)
        outer.addWidget(top)

        self._empty = QLabel(
            "No recipes yet. Set a chain up in the Job Queue, then choose "
            "“Save chain as recipe…” from one of its jobs' ⋯ menu.", self)
        self._empty.setProperty("textRole", "help")
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        self._list = QListWidget(self)
        self._list.setObjectName("RecipeList")
        self._list.setMaximumHeight(_RECIPE_LIST_MAX_H)
        self._list.currentItemChanged.connect(self._on_current_changed)
        outer.addWidget(self._list)

        self._detail = QWidget(self)
        dl = QVBoxLayout(self._detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(theme.Spacing.sm)
        self._name = QLabel("", self._detail)
        self._name.setProperty("textRole", "sectionHeading")
        self._name.setWordWrap(True)
        dl.addWidget(self._name)
        self._origin = QLabel("", self._detail)
        self._origin.setProperty("textRole", "help")
        self._origin.setWordWrap(True)
        dl.addWidget(self._origin)

        actions = QWidget(self._detail)
        al = FlowLayout(actions, h_spacing=theme.Spacing.sm, v_spacing=theme.Spacing.xs)
        self._buttons: dict[str, QPushButton] = {}
        for key, text, variant, signal in (
                ("apply", "▶  Apply to…", "primary", self.applyRequested),
                ("rename", "Rename…", "default", self.renameRequested),
                ("duplicate", "Duplicate", "default", self.duplicateRequested),
                ("export", "Export…", "default", self.exportRequested),
                ("delete", "Delete…", "destructive", self.deleteRequested)):
            button = QPushButton(text, actions)
            button.setProperty("variant", variant)
            button.clicked.connect(lambda _checked=False, s=signal: s.emit(self._current))
            al.addWidget(button)
            self._buttons[key] = button
        dl.addWidget(actions)

        steps_title = QLabel("Steps", self._detail)
        steps_title.setProperty("textRole", "sectionHeading")
        dl.addWidget(steps_title)
        self._steps = QLabel("", self._detail)
        self._steps.setObjectName("RecipeSteps")
        self._steps.setWordWrap(True)
        self._steps.setTextFormat(Qt.PlainText)
        self._steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self._steps)
        self._notes = QLabel("", self._detail)
        self._notes.setProperty("textRole", "help")
        self._notes.setWordWrap(True)
        dl.addWidget(self._notes)

        self._open_result = QCheckBox(
            "Open the result in Review when the last Analyze step finishes", self._detail)
        self._open_result.setObjectName("RecipeOpenResult")
        self._open_result.setToolTip(
            "Switches to Review only if nobody is using the app at that moment. "
            "If you are working in another pane, the result loads in Review behind "
            "you; if you are reading another dataset in Review, it is left alone and "
            "the run says the result is ready. Applies to runs applied from now on.")
        self._open_result.toggled.connect(
            lambda on: self.openResultToggled.emit(self._current, on))
        dl.addWidget(self._open_result)
        self._open_result_note = QLabel(
            "This recipe has no Analyze step, so there is no result to open.", self._detail)
        self._open_result_note.setProperty("textRole", "help")
        self._open_result_note.setWordWrap(True)
        dl.addWidget(self._open_result_note)

        dl.addWidget(_hrule(self._detail))
        self._runs_line = QLabel("", self._detail)
        self._runs_line.setProperty("textRole", "help")
        self._runs_line.setWordWrap(True)
        dl.addWidget(self._runs_line)
        self._runs_btn = QPushButton("Show runs", self._detail)
        self._runs_btn.setProperty("variant", "default")
        self._runs_btn.setToolTip("Every time this recipe was applied, with each run's log.")
        self._runs_btn.clicked.connect(lambda: self.showRunsRequested.emit(self._current))
        dl.addWidget(self._runs_btn, 0, Qt.AlignLeft)
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

    def set_open_result(self, on: bool, available: bool) -> None:
        """The checkbox's state for the selected recipe, without echoing it
        back as a toggle; *available* is whether it has an Analyze step."""
        self._open_result.blockSignals(True)
        self._open_result.setChecked(bool(on) and available)
        self._open_result.blockSignals(False)
        self._open_result.setEnabled(available)
        self._open_result_note.setVisible(not available)

    def open_result_box(self) -> QCheckBox:
        return self._open_result

    def set_runs_line(self, text: str, has_runs: bool) -> None:
        """One line about the selected recipe's runs, and whether to offer them."""
        self._runs_line.setText(text)
        self._runs_btn.setVisible(has_runs)

    def runs_line(self) -> str:
        return self._runs_line.text()

    def steps_text(self) -> str:
        return self._steps.text()

    def button(self, key: str) -> QPushButton:
        return self._buttons[key]

    # ── internals ────────────────────────────────────────────────────────────
    def _on_current_changed(self, current, _previous) -> None:
        self._current = str(current.data(Qt.UserRole)) if current is not None else ""
        if self._current:
            self.recipeSelected.emit(self._current)

