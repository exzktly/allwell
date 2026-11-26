"""The Job Queue page's recipe actions: save a chain, apply one, delete one.

The model — what a recipe keeps, drops and templates — is
:mod:`services.job_recipes`, Qt-free. This module is the part that asks the
user for things (a name, an input, a confirmation) and hands the result to the
queue bridge. Each question goes through its own method (:meth:`_ask_name`,
:meth:`_ask_input`, :meth:`_confirm_delete`) so a test can answer it the way a
user would, without a modal dialog.

Applying a recipe adds its jobs **with** their links, one at a time in order
(``QueueBridge.add_job``), so a running queue can never start a later step
against the placeholder path its input shows before the step it reads from has
run.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from services.job_recipes import (
    Recipe,
    RecipeError,
    export_json,
    import_json,
    instantiate,
    load_recipes,
    recipe_from_chain,
    save_recipes,
    unused_name,
    with_recipe,
)
from services import recipe_journal
from services.job_kinds import KIND_LABEL, short_label
from util.json_write import write_json_atomic


class RecipeActions:
    """Recipes as the Job Queue page offers them."""

    def __init__(self, bridge, *, state_dir=None,
                 notify: Callable[[str, str], None],
                 parent_widget: Callable[[], object]) -> None:
        self._bridge = bridge
        self._state_dir = state_dir
        self._notify = notify
        self._parent = parent_widget
        self._recipes: list[Recipe] = load_recipes(state_dir)
        self._listeners: list[Callable[[], None]] = []

    @property
    def recipes(self) -> list[Recipe]:
        return list(self._recipes)

    def get(self, name: str) -> Recipe | None:
        return next((r for r in self._recipes if r.name == name), None)

    # ── save ─────────────────────────────────────────────────────────────────
    def save_from(self, job_id: str) -> Recipe | None:
        """Save the chain *job_id* is part of under a name the user gives."""
        jobs = self._bridge.queue.jobs
        try:
            # Built once with a placeholder name first, so a chain that cannot
            # be a recipe is refused before the user is asked to name it.
            draft = recipe_from_chain("draft", jobs, job_id)
        except RecipeError as exc:
            self._notify(str(exc), "warn")
            return None
        name = self._ask_name(draft.summary())
        if not name:
            return None
        recipe = recipe_from_chain(name, jobs, job_id)
        replaced = self.get(recipe.name) is not None
        self._recipes = with_recipe(self._recipes, recipe)
        self._changed()
        verb = "Replaced" if replaced else "Saved"
        self._notify(f"{verb} the recipe “{recipe.name}” ({recipe.summary()}). "
                     "Apply it from Recipes.", "success")
        return recipe

    # ── apply ────────────────────────────────────────────────────────────────
    def apply(self, name: str, source: str | None = None) -> list[str]:
        """Queue *name*'s chain on *source* (asked for when not given).
        Returns the ids of the jobs added."""
        recipe = self.get(name)
        if recipe is None:
            self._notify(f"There is no recipe called “{name}” any more.", "warn")
            return []
        if source is None:
            source = self._ask_input(recipe)
        if not source:
            return []
        try:
            planned = instantiate(recipe, source, lambda: uuid.uuid4().hex[:10])
        except RecipeError as exc:
            self._notify(str(exc), "warn")
            return []
        run_id = uuid.uuid4().hex[:12]
        for step, (job, _links) in enumerate(planned):
            job.recipe = {"run": run_id, "name": recipe.name, "input": source,
                          "step": step, "steps": len(planned)}
        # Journalled before the jobs are added: a running queue starts the
        # first of them inside add_job, and its "started" must come after this.
        recipe_journal.append(run_id, "applied", state_dir=self._state_dir,
                              name=recipe.name, input=source,
                              jobs=[job.id for job, _links in planned],
                              notes=list(recipe.notes))
        added: list[str] = []
        refused: list[str] = []
        for job, links in planned:
            refused.extend(self._bridge.add_job(job, links))
            added.append(job.id)
        message = (f"Queued {len(added)} job(s) from “{recipe.name}” for "
                   f"{Path(source).name}.")
        if refused:
            message += (f" {len(refused)} link(s) could not be made; those inputs "
                        "use the folder shown on the job's page.")
        if recipe.notes:
            message += " " + " ".join(recipe.notes)
        self._notify(message, "warn" if refused else "success")
        return added

    # ── the run's log ────────────────────────────────────────────────────────
    def run_of(self, job_id: str) -> str:
        """The recipe run *job_id* belongs to, or ``""``."""
        job = self._bridge.queue.get(job_id)
        return str((job.recipe or {}).get("run") or "") if job is not None else ""

    def run_log_text(self, run_id: str) -> str:
        """The journal of *run_id*, as text, headed by what it was."""
        summary = recipe_journal.summarise(run_id, self._state_dir)
        if summary is None:
            return "No log was recorded for this run."
        labels = {j.id: j.label for j in self._bridge.queue.jobs}
        head = (f"Recipe “{summary.name}” applied to {summary.input} — "
                f"{summary.done}/{len(summary.steps)} steps done, {summary.status}\n"
                f"Journal: {recipe_journal.journal_path(run_id, self._state_dir)}\n\n")
        return head + recipe_journal.render(run_id, self._state_dir, labels)

    # ── manage ───────────────────────────────────────────────────────────────
    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call *listener* whenever the saved recipes change, from any page."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        save_recipes(self._recipes, self._state_dir)
        for listener in list(self._listeners):
            listener()

    def rename(self, old: str, new: str) -> bool:
        new = (new or "").strip()
        recipe = self.get(old)
        if recipe is None or not new or new == old:
            return False
        if self.get(new) is not None:
            self._notify(f"There is already a recipe called “{new}”.", "warn")
            return False
        recipe.name = new
        self._changed()
        self._notify(f"Renamed “{old}” to “{new}”. Its earlier runs keep the old name "
                     "in their logs.", "success")
        return True

    def duplicate(self, name: str) -> Recipe | None:
        recipe = self.get(name)
        if recipe is None:
            return None
        copy = Recipe.from_json(json.loads(json.dumps(recipe.to_json())))
        copy.name = unused_name(self._recipes, f"{name} copy")
        self._recipes = [*self._recipes, copy]
        self._changed()
        return copy

    def export_to(self, name: str, path: str) -> bool:
        recipe = self.get(name)
        if recipe is None or not path:
            return False
        try:
            write_json_atomic(Path(path), export_json(recipe))
        except OSError as exc:
            self._notify(f"Could not write {path}: {exc}", "danger")
            return False
        self._notify(f"Exported “{name}” to {path}.", "success")
        return True

    def import_from(self, path: str) -> Recipe | None:
        if not path:
            return None
        try:
            recipe = import_json(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            self._notify(f"Could not import {Path(path).name}: {exc}", "warn")
            return None
        recipe.name = unused_name(self._recipes, recipe.name)
        self._recipes = [*self._recipes, recipe]
        self._changed()
        self._notify(f"Imported “{recipe.name}” ({recipe.summary()}).", "success")
        return recipe

    # ── delete ───────────────────────────────────────────────────────────────
    def delete(self, name: str) -> bool:
        if self.get(name) is None or not self._confirm_delete(name):
            return False
        self._recipes = [r for r in self._recipes if r.name != name]
        self._changed()
        self._notify(f"Deleted the recipe “{name}”.", "success")
        return True

    # ── the questions, one seam each ─────────────────────────────────────────
    def _ask_name(self, suggestion: str) -> str:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self._parent(), "Save chain as recipe",
            "Name this recipe. Applying it later queues the same chain, with "
            "its settings, on a folder or file you choose:", text=suggestion)
        return text.strip() if ok else ""

    def _ask_input(self, recipe: Recipe) -> str:
        from PySide6.QtWidgets import QFileDialog

        first = KIND_LABEL.get(recipe.root_kind, recipe.root_kind)
        start = str(Path(recipe.saved_from).parent) if recipe.saved_from else ""
        title = f"Apply “{short_label(recipe.name)}” — choose the input for {first}"
        if recipe.wants_file():
            path, _filter = QFileDialog.getOpenFileName(self._parent(), title, start)
            return path
        return QFileDialog.getExistingDirectory(self._parent(), title, start)

    def _confirm_delete(self, name: str) -> bool:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self._parent(), "Delete recipe?",
            f"Delete the recipe “{name}”? Jobs already queued from it are not "
            "affected.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes
