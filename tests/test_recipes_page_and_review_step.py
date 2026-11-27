"""The Recipes and Runs tabs, recipe sharing, run summaries, and a recipe's
"open the result in Review".

The page tests build the real ``DatasetsTab`` and drive the tabs the way a
user would, answering their questions through the controller's seams. The
review tests drive a real ``QueueBridge`` through a recipe run whose Analyze
step reports an analysed dataset, with a stand-in for the shell's hand-off.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from core.user_activity import review_hand_off
from datasets.job_queue.controller import QueueBridge
from datasets.job_queue.review_handoff import reviewable_root
from services import recipe_journal
from services.job_kinds import CHAIN_KIND
from services.job_queue import JobQueue, JobSpec, JobState
from services.job_recipes import (
    RecipeError,
    export_json,
    import_json,
    recipe_from_chain,
    save_recipes,
    unused_name,
)


def _chain(root: str) -> list[JobSpec]:
    q = JobQueue()
    q.add(JobSpec(id="w", kind="watch_folder", label="w", spec={"folder": root}))
    q.add(JobSpec(id="c", kind="nd2_import", label="c", spec={
        "source": f"{root}/run7.nd2", "destination": f"{root}_dataset"}))
    q.add(JobSpec(id="a", kind="analyze", label="a",
                  spec={"opts": {"raw": f"{root}_dataset"}}))
    q.set_link("c", "source", "w", kind=CHAIN_KIND)
    q.set_link("a", "opts.raw", "c", kind=CHAIN_KIND)
    return q.jobs


# ── run summaries ─────────────────────────────────────────────────────────────

def _events(tmp_path, run, *events):
    for i, (event, fields) in enumerate(events):
        recipe_journal.append(run, event, state_dir=tmp_path, now=100.0 + i, **fields)


def test_a_run_summary_follows_its_steps(tmp_path):
    _events(tmp_path, "r1",
            ("applied", {"name": "N", "input": "/x/run8", "jobs": ["a", "b", "c"]}),
            ("started", {"step": 0}), ("finished", {"step": 0, "ok": True, "wrote": "/x/run8"}),
            ("started", {"step": 1}), ("finished", {"step": 1, "ok": False, "message": "boom"}))
    s = recipe_journal.summarise("r1", tmp_path)
    assert s.steps == ["succeeded", "failed", "waiting"]
    assert (s.done, s.status, s.results()) == (1, "failed", ["/x/run8"])
    _events(tmp_path, "r1", ("retried", {"step": 1}), ("started", {"step": 1}),
            ("finished", {"step": 1, "ok": True, "wrote": "/x/run8_dataset"}),
            ("started", {"step": 2}), ("finished", {"step": 2, "ok": True, "wrote": "/x/run8_dataset"}))
    s = recipe_journal.summarise("r1", tmp_path)
    assert (s.done, s.status) == (3, "done")


def test_runs_are_listed_newest_first_and_by_recipe(tmp_path):
    recipe_journal.append("old", "applied", state_dir=tmp_path, now=1.0, name="A", jobs=["x"])
    recipe_journal.append("new", "applied", state_dir=tmp_path, now=2.0, name="A", jobs=["x"])
    recipe_journal.append("other", "applied", state_dir=tmp_path, now=3.0, name="B", jobs=["x"])
    assert [r.run for r in recipe_journal.list_runs(tmp_path, name="A")] == ["new", "old"]


# ── sharing ───────────────────────────────────────────────────────────────────

def test_an_exported_recipe_imports_unchanged():
    recipe = recipe_from_chain("Nightly", _chain("/scope/run7"), "w")
    data = json.loads(json.dumps(export_json(recipe)))
    assert import_json(data).to_json() == recipe.to_json()


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.pop("allwell_recipe"), "isn't an All-Well recipe"),
    (lambda d: d.update(allwell_recipe=99), "format 99"),
    (lambda d: d["recipe"]["steps"][1].update(kind="teleport"), r"doesn't have \(teleport\)"),
    (lambda d: d["recipe"]["steps"][1]["links"][0].update(step=2), "link is damaged"),
    (lambda d: d["recipe"].update(steps=[]), "no name or no steps"),
])
def test_a_damaged_recipe_file_is_refused_naming_why(mutate, message):
    data = export_json(recipe_from_chain("Nightly", _chain("/scope/run7"), "w"))
    mutate(data)
    with pytest.raises(RecipeError, match=message):
        import_json(data)


def test_an_imported_name_never_replaces_one_you_have():
    recipes = [recipe_from_chain(n, _chain("/s/run7"), "w") for n in ("N", "N (2)")]
    assert unused_name(recipes, "N") == "N (3)"
    assert unused_name(recipes, "M") == "M"


# ── the page ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tab(qtbot, tmp_path):
    from datasets.tab import DatasetsTab

    shown: list = []
    t = DatasetsTab(queue_state_dir=tmp_path / "state",
                    on_show_in_review=lambda path, when_idle: shown.append(
                        (path, when_idle)) or "switched")
    t.shown_in_review = shown
    yield t
    t.shutdown()
    qtbot.wait(50)


def _quiet(tab, monkeypatch):
    notes: list = []
    monkeypatch.setattr(tab._controllers["job_queue"], "_notify",
                        lambda text, kind="warn": notes.append((kind, text)))
    return notes


def _with_recipe(tab, tmp_path, name="Nightly"):
    state = tmp_path / "state"
    save_recipes([recipe_from_chain(name, _chain(str(tmp_path / "run7")), "w")], state)
    actions = tab._controllers["job_queue"].recipes
    actions._recipes = __import__("services.job_recipes", fromlist=["x"]).load_recipes(state)
    actions._changed()
    return tab._controllers["recipes"]


def test_the_page_shows_a_recipe_s_steps_in_words(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    page = _with_recipe(tab, tmp_path).page
    assert page.current_recipe() == "Nightly"
    steps = page.steps_text().splitlines()
    assert steps[0].startswith("1. Watch folder — ‹input› · settled after")
    assert steps[1] == ("2. Convert ND2 — ‹input›/run7.nd2 → ‹input's folder›/‹input name›_dataset"
                        " — source from step 1")
    assert steps[2].startswith("3. Analyze — ") and steps[2].endswith("input from step 2")


def test_applying_from_the_page_lists_the_run_and_its_log(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    recipes = _with_recipe(tab, tmp_path)
    queue = tab._controllers["job_queue"]
    run8 = tmp_path / "run8"
    monkeypatch.setattr(queue.recipes, "_ask_input", lambda recipe: str(run8))
    recipes.page.button("apply").click()
    jobs = queue.bridge.queue.jobs
    assert [j.kind for j in jobs] == ["watch_folder", "nd2_import", "analyze"]
    # The jobs it added are what to look at next.
    assert tab._automation.current_tab() == "Queue"
    run = jobs[0].recipe["run"]
    assert recipes.page.runs_line().startswith("Applied once; last on ")
    assert recipes.page.runs_line().endswith("— 0/3 steps, waiting.")
    recipes.page.showRunsRequested.emit("Nightly")
    assert tab._automation.current_tab() == "Runs"
    assert recipes.runs_page.current_filter() == "Nightly"
    assert len(recipes.runs_page.run_lines()) == 1
    recipes._refresh_runs(select=run)
    assert recipes.runs_page.current_run() == run
    assert "applied “Nightly”" in recipes.runs_page.log_text()
    assert "0/3 steps done, waiting" in recipes.runs_page.log_text()


def test_a_row_s_recipe_log_opens_its_run_on_the_page(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    recipes = _with_recipe(tab, tmp_path)
    queue = tab._controllers["job_queue"]
    monkeypatch.setattr(queue.recipes, "_ask_input", lambda recipe: str(tmp_path / "run8"))
    queue.recipes.apply("Nightly")
    job = queue.bridge.queue.jobs[1]
    menu = queue.page._rows[job.id].build_menu()
    next(a for a in menu.actions() if a.text() == "Recipe log…").trigger()
    assert tab._pages.currentWidget() is tab._automation
    assert tab._automation.current_tab() == "Runs"
    assert recipes.runs_page.current_run() == job.recipe["run"]
    assert "applied “Nightly”" in recipes.runs_page.log_text()


def test_rename_duplicate_export_import_from_the_page(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    recipes = _with_recipe(tab, tmp_path)
    queue = tab._controllers["job_queue"]
    monkeypatch.setattr(recipes, "_ask_rename", lambda name: "Overnight")
    recipes.page.button("rename").click()
    assert [r.name for r in queue.recipes.recipes] == ["Overnight"]
    assert any(a.text() == "Apply “Overnight”…"
               for a in queue.page.build_recipes_menu().actions())
    recipes.page.button("duplicate").click()
    assert [r.name for r in queue.recipes.recipes] == ["Overnight", "Overnight copy"]
    target = tmp_path / "shared.json"
    monkeypatch.setattr(recipes, "_ask_export_path", lambda name: str(target))
    recipes.page.button("export").click()
    monkeypatch.setattr(recipes, "_ask_import_path", lambda: str(target))
    recipes.page.importRequested.emit()
    names = [r.name for r in queue.recipes.recipes]
    # The copy was selected, so it was the one exported, and its name is taken.
    assert names == ["Overnight", "Overnight copy", "Overnight copy (2)"]
    assert recipes.page.current_recipe() == "Overnight copy (2)"


def test_a_rename_to_a_taken_name_is_refused(tab, tmp_path, monkeypatch):
    notes = _quiet(tab, monkeypatch)
    recipes = _with_recipe(tab, tmp_path)
    queue = tab._controllers["job_queue"]
    queue.recipes.duplicate("Nightly")
    assert queue.recipes.rename("Nightly copy", "Nightly") is False
    assert notes[-1] == ("warn", "There is already a recipe called “Nightly”.")
    assert recipes is not None


# ── "open the result in Review" ───────────────────────────────────────────────

def test_only_an_analysed_dataset_is_reviewable(tmp_path):
    (tmp_path / "done" / "out").mkdir(parents=True)
    (tmp_path / "done" / "out" / "pipeline_info.json").write_text("{}")
    (tmp_path / "converted" / "in").mkdir(parents=True)
    assert reviewable_root(tmp_path / "done") == tmp_path / "done"
    assert reviewable_root(tmp_path / "done" / "out") == tmp_path / "done"
    assert reviewable_root(tmp_path / "converted") is None


@pytest.mark.parametrize("when_idle, idle, on_review, outcome", [
    (False, False, True, "switched"),     # asked by a person
    (True, True, True, "switched"),       # nobody is using the app
    (True, False, False, "loaded"),       # busy in Analyze or Datasets
    (True, False, True, "held"),          # reading Review itself
])
def test_the_hand_off_moves_the_window_only_when_nobody_is_using_it(
        when_idle, idle, on_review, outcome):
    assert review_hand_off(when_idle=when_idle, idle=idle, on_review=on_review) == outcome


def test_apply_marks_only_the_last_analyze_step_and_only_when_asked(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    _with_recipe(tab, tmp_path)
    queue = tab._controllers["job_queue"]
    monkeypatch.setattr(queue.recipes, "_ask_input", lambda recipe: str(tmp_path / "run8"))
    queue.recipes.apply("Nightly")
    assert [bool(j.recipe.get("open_result")) for j in queue.bridge.queue.jobs] == [
        False, False, False]
    assert queue.recipes.set_open_result("Nightly", True)
    queue.recipes.apply("Nightly")
    assert [bool(j.recipe.get("open_result")) for j in queue.bridge.queue.jobs[3:]] == [
        False, False, True]


def test_the_checkbox_is_saved_with_the_recipe(tab, tmp_path, monkeypatch):
    from services.job_recipes import load_recipes

    _quiet(tab, monkeypatch)
    recipes = _with_recipe(tab, tmp_path)
    box = recipes.page.open_result_box()
    assert box.isEnabled() and not box.isChecked()
    box.setChecked(True)
    assert load_recipes(tmp_path / "state")[0].open_result is True
    # And it travels with an export.
    data = export_json(load_recipes(tmp_path / "state")[0])
    assert import_json(json.loads(json.dumps(data))).open_result is True


def test_without_an_analyze_step_there_is_nothing_to_open(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    q = JobQueue()
    q.add(JobSpec(id="w", kind="watch_folder", label="w", spec={"folder": str(tmp_path / "r7")}))
    save_recipes([recipe_from_chain("Watch only", q.jobs, "w")], tmp_path / "state")
    actions = tab._controllers["job_queue"].recipes
    actions._recipes = __import__("services.job_recipes", fromlist=["x"]).load_recipes(
        tmp_path / "state")
    actions._changed()
    assert not tab._controllers["recipes"].page.open_result_box().isEnabled()


def _analysed(tmp_path, name="run8_dataset"):
    root = tmp_path / name
    (root / "out").mkdir(parents=True)
    (root / "out" / "pipeline_info.json").write_text("{}")
    return root


class _Analyze(QObject):
    jobFinished = Signal(bool, str, str)

    def start_from_queue(self, spec):
        self.spec = spec

    def show_job(self, spec):
        pass

    def stop(self):
        pass

    def is_busy(self):
        return False


def _run_analyze(tmp_path, *, open_result: bool, outcome: str = "loaded", report=None):
    bridge = QueueBridge(state_dir=tmp_path / "state")
    analyze = _Analyze()
    calls: list = []
    held: list = []
    bridge.review_hook = lambda root, when_idle: calls.append((root, when_idle)) or outcome
    bridge.on_review_held = held.append
    bridge.register("analyze", analyze)
    recipe = {"run": "r9", "name": "N", "input": "/x", "step": 0, "steps": 1}
    if open_result:
        recipe["open_result"] = True
    bridge.add_job(JobSpec(id="a", kind="analyze", label="a",
                           spec={"opts": {"raw": "/planned/in"}}, recipe=recipe))
    bridge.start()
    analyze.jobFinished.emit(True, "done", report)
    return bridge, calls, held


def test_a_finished_analysis_is_opened_when_the_recipe_asks(tmp_path, qtbot):
    root = _analysed(tmp_path)
    bridge, calls, held = _run_analyze(tmp_path, open_result=True, report=str(root / "out"))
    assert calls == [(root, True)]
    job = bridge.queue.get("a")
    assert job.state == JobState.SUCCEEDED
    assert job.message == f"done · Loaded {root} in Review. You were using the app, so " \
                          "the window stayed where it was — switch to Review to see it."
    statuses = [e["message"] for e in recipe_journal.read_events("r9", tmp_path / "state")
                if e["event"] == "status"]
    assert statuses == [f"Loaded {root} in Review. You were using the app, so the window "
                        "stayed where it was — switch to Review to see it."]
    assert held == []


def test_review_left_alone_says_the_result_is_ready(tmp_path, qtbot):
    root = _analysed(tmp_path)
    _bridge, _calls, held = _run_analyze(tmp_path, open_result=True, outcome="held",
                                         report=str(root / "out"))
    assert held == [f"Review was in use, so it was left as it was. The result is ready "
                    f"to open: {root}"]


def test_nothing_is_opened_unless_the_recipe_asked(tmp_path, qtbot):
    root = _analysed(tmp_path)
    _bridge, calls, _held = _run_analyze(tmp_path, open_result=False, report=str(root / "out"))
    assert calls == []


def test_a_result_with_nothing_analysed_is_said_not_opened(tmp_path, qtbot):
    (tmp_path / "half" / "out").mkdir(parents=True)
    bridge, calls, _held = _run_analyze(tmp_path, open_result=True,
                                        report=str(tmp_path / "half" / "out"))
    assert calls == []
    assert "Not opened in Review" in bridge.queue.get("a").message
    assert "holds no analysed results" in bridge.queue.get("a").message


def test_a_retried_step_no_longer_offers_what_it_wrote_before(tmp_path):
    _events(tmp_path, "r2",
            ("applied", {"name": "N", "input": "/x", "jobs": ["a"]}),
            ("finished", {"step": 0, "ok": True, "wrote": "/x/old_result"}),
            ("retried", {"step": 0}))
    s = recipe_journal.summarise("r2", tmp_path)
    assert (s.steps, s.results()) == (["waiting"], [])


def test_runs_of_a_deleted_recipe_stay_listed_under_their_name(tab, tmp_path, monkeypatch):
    _quiet(tab, monkeypatch)
    recipes = _with_recipe(tab, tmp_path)
    queue = tab._controllers["job_queue"]
    monkeypatch.setattr(queue.recipes, "_ask_input", lambda recipe: str(tmp_path / "run8"))
    queue.recipes.apply("Nightly")
    monkeypatch.setattr(queue.recipes, "_confirm_delete", lambda name: True)
    queue.recipes.delete("Nightly")
    assert recipes.runs_page.current_filter() == "All recipes"
    lines = recipes.runs_page.run_lines()
    assert len(lines) == 1 and lines[0].startswith("Nightly · ")


def test_the_page_is_one_sidebar_entry_with_three_tabs(tab):
    from datasets.tab import _FEATURES

    names = [f.name for f in _FEATURES]
    assert "Automation" in names and "Job Queue" not in names and "Recipes" not in names
    page = tab._automation
    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == ["Queue", "Recipes", "Runs"]
    assert page.tabs.widget(0) is tab._controllers["job_queue"].page
