"""Wires the Recipes page to the recipe store, the queue and the run journals.

The page shares the Job Queue's :class:`RecipeActions` rather than holding a
second copy: a recipe saved from a queue row appears here at once, and one
renamed or deleted here is gone from the queue's Apply menu, because both
listen to the same store (``RecipeActions.subscribe``). Runs are read from
their journals (``services.recipe_journal.list_runs``) and re-read whenever the
queue changes, so a run being worked through updates in place.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject

from services import recipe_journal
from services.job_kinds import KIND_LABEL, describe_detail, input_label
from datasets.job_queue.recipe_actions import RecipeActions
from datasets.job_queue.recipes_view import RecipesPage, readable


def _when(ts: float | None) -> str:
    if ts is None:
        return "?"
    return time.strftime("%d %b %H:%M", time.localtime(ts))


def run_line(summary: recipe_journal.RunSummary) -> str:
    """One run as its list row: when, on what, how far, how it stands."""
    from pathlib import Path

    target = Path(summary.input).name or summary.input
    return (f"{_when(summary.applied_at)} · {target} · "
            f"{summary.done}/{len(summary.steps)} steps · {summary.status}")


class RecipesController(QObject):
    """Behaviour for the Recipes page."""

    def __init__(self, actions: RecipeActions, bridge, *, state_dir=None,
                 show_queue: Callable[[], None] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._actions = actions
        self._bridge = bridge
        self._state_dir = state_dir
        self._show_queue = show_queue or (lambda: None)

        self.page = RecipesPage()
        self.page.recipeSelected.connect(self._show_recipe)
        self.page.runSelected.connect(self._show_run)
        self.page.applyRequested.connect(self._on_apply)
        self.page.renameRequested.connect(self._on_rename)
        self.page.duplicateRequested.connect(self._on_duplicate)
        self.page.exportRequested.connect(self._on_export)
        self.page.deleteRequested.connect(self._actions.delete)
        self.page.importRequested.connect(self._on_import)
        self.page.showQueueRequested.connect(self._show_queue)
        actions.subscribe(self.refresh)
        bridge.queueChanged.connect(self._refresh_runs)
        self.refresh()

    # ── showing ──────────────────────────────────────────────────────────────
    def refresh(self, select: str = "") -> None:
        self.page.set_recipes([(r.name, r.summary()) for r in self._actions.recipes],
                              select=select)
        if not self._actions.recipes:
            self.page.set_runs([])

    def _show_recipe(self, name: str) -> None:
        recipe = self._actions.get(name)
        if recipe is None:
            return
        lines = []
        for step in recipe.steps:
            line = KIND_LABEL.get(step.kind, step.kind)
            detail = readable(describe_detail(step.kind, step.spec))
            linked = [f"{input_label(step.kind, link.target).lower()} from step {link.step + 1}"
                      for link in step.links]
            line += "".join(f" — {part}" for part in (detail, *linked) if part)
            lines.append(line)
        origin = (f"{recipe.summary()} · saved from {recipe.saved_from}"
                  if recipe.saved_from else recipe.summary())
        self.page.show_recipe(recipe.name, origin, lines, recipe.notes)
        self._refresh_runs()

    def _refresh_runs(self, select: str = "") -> None:
        name = self.page.current_recipe()
        if not name:
            return
        runs = recipe_journal.list_runs(self._state_dir, name=name)
        self.page.set_runs([(r.run, run_line(r)) for r in runs], select=select)
        current = select or self.page.current_run()
        if current:
            self._show_run(current)

    def _show_run(self, run_id: str) -> None:
        self.page.show_log(self._actions.run_log_text(run_id))

    def select_run(self, run_id: str) -> bool:
        """Show *run_id*: its recipe selected, the run selected, its log open.
        Returns whether the run was found (a run of a recipe since deleted or
        renamed still has a journal, under the name it was applied with)."""
        summary = recipe_journal.summarise(run_id, self._state_dir)
        if summary is None or self._actions.get(summary.name) is None:
            return False
        self.refresh(select=summary.name)
        self._refresh_runs(select=run_id)
        return True

    # ── actions ──────────────────────────────────────────────────────────────
    def _on_apply(self, name: str) -> None:
        if self._actions.apply(name):
            self._refresh_runs()

    def _on_rename(self, name: str) -> None:
        new = self._ask_rename(name)
        if new and self._actions.rename(name, new):
            self.refresh(select=new.strip())

    def _on_duplicate(self, name: str) -> None:
        copy = self._actions.duplicate(name)
        if copy is not None:
            self.refresh(select=copy.name)

    def _on_export(self, name: str) -> None:
        path = self._ask_export_path(name)
        if path:
            self._actions.export_to(name, path)

    def _on_import(self) -> None:
        recipe = self._actions.import_from(self._ask_import_path())
        if recipe is not None:
            self.refresh(select=recipe.name)

    # ── the questions, one seam each ─────────────────────────────────────────
    def _ask_rename(self, name: str) -> str:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self.page.window(), "Rename recipe",
                                        "New name:", text=name)
        return text if ok else ""

    def _ask_export_path(self, name: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getSaveFileName(
            self.page.window(), "Export recipe", f"{name}.allwell-recipe.json",
            "All-Well recipe (*.json)")
        return path

    def _ask_import_path(self) -> str:
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getOpenFileName(
            self.page.window(), "Import recipe", "", "All-Well recipe (*.json)")
        return path

    def shutdown(self) -> None:
        """Nothing runs here; present for the page roster's teardown."""
