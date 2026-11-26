"""Job Queue recipes: save a chain as a template, queue it again on a new input.

The model tests build the chain the feature exists for — Watch folder →
Convert ND2 → Analyze — with paths that sit beside and inside the watched
folder, so templating that missed either kind would show. The page tests go
through the real ``DatasetsTab`` and its Job Queue page, answering the name
and folder questions the way a user would.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from services.job_kinds import CHAIN_KIND
from services.job_queue import ChainLink, JobQueue, JobSpec, JobState
from services.job_recipes import (
    Recipe,
    RecipeError,
    instantiate,
    load_recipes,
    recipe_from_chain,
    recipes_path,
    save_recipes,
    source_name,
)


def _chain(root="/scope/run7") -> list[JobSpec]:
    """Watch → Convert ND2 → Analyze, linked, as the queue would hold them."""
    q = JobQueue()
    q.add(JobSpec(id="w", kind="watch_folder", label="w",
                  spec={"folder": root, "quiet_minutes": 10.0, "require_change": True}))
    q.add(JobSpec(id="c", kind="nd2_import", label="c", spec={
        "source": f"{root}/run7.nd2",
        "destination": f"{os.path.dirname(root)}/run7_dataset",
        "params": {"experiment": "run7", "timepoint_subset": ["0h", "24h"],
                   "timepoint_stride": 2, "well_subset": ["A01"], "grid_rows": 8},
        "channel_roles": [["DAPI", "nuclear"], ["GFP", "fluor"]],
        "planned_wells": ["A01", "A02"],
    }))
    q.add(JobSpec(id="a", kind="analyze", label="a", spec={
        "opts": {"raw": f"{os.path.dirname(root)}/run7_dataset", "nuclear_token": "DAPI",
                 "force_wells": ["B03"], "skip_wells": ["H12"], "reference": "/refs/flat.tif"},
        "remote_name": "cluster",
        "upstream_layout": {"schema": "x"},
    }))
    q.set_link("c", "source", "w", kind=CHAIN_KIND)
    q.set_link("a", "opts.raw", "c", kind=CHAIN_KIND)
    return q.jobs


def test_a_chain_is_saved_with_its_links_by_position():
    recipe = recipe_from_chain("Nightly ND2", _chain(), "c")
    assert [s.kind for s in recipe.steps] == ["watch_folder", "nd2_import", "analyze"]
    assert [[(link.target, link.step) for link in s.links] for s in recipe.steps] == [
        [], [("source", 0)], [("opts.raw", 1)]]
    assert recipe.summary() == "Watch folder → Convert ND2 → Analyze"
    assert recipe.saved_from == "/scope/run7"


def test_paths_are_templated_on_the_chain_s_input():
    steps = recipe_from_chain("r", _chain(), "a").steps
    assert steps[0].spec["folder"] == "{source}"
    assert steps[1].spec["source"] == "{source}/run7.nd2"
    assert steps[1].spec["destination"] == "{source_parent}/{source_name}_dataset"
    # A path that has nothing to do with the input is kept as it is.
    assert steps[2].spec["opts"]["reference"] == "/refs/flat.tif"


def test_what_belongs_to_one_acquisition_is_left_out_and_said():
    recipe = recipe_from_chain("r", _chain(), "w")
    convert, analyze = recipe.steps[1].spec, recipe.steps[2].spec
    assert convert["params"]["experiment"] == ""
    assert convert["params"]["timepoint_subset"] == []
    assert "planned_wells" not in convert
    # Settings that describe the protocol rather than the acquisition stay.
    assert convert["params"]["timepoint_stride"] == 2
    assert convert["params"]["well_subset"] == ["A01"]
    assert convert["channel_roles"] == [["DAPI", "nuclear"], ["GFP", "fluor"]]
    assert analyze["opts"]["force_wells"] == []
    assert analyze["opts"]["skip_wells"] == ["H12"]
    assert analyze["remote_name"] == "cluster"
    assert "upstream_layout" not in analyze
    assert analyze["auto_detect"] is True
    assert any("experiment name" in note for note in recipe.notes)
    assert any("wells to redo" in note for note in recipe.notes)
    assert any("schema and channels" in note for note in recipe.notes)


def test_applying_a_recipe_queues_the_chain_on_the_new_input():
    recipe = recipe_from_chain("r", _chain(), "w")
    ids = iter(["W", "C", "A"])
    planned = instantiate(recipe, "/scope/run8", lambda: next(ids))
    jobs = [job for job, _links in planned]
    assert [j.id for j in jobs] == ["W", "C", "A"]
    assert jobs[0].spec["folder"] == "/scope/run8"
    assert jobs[1].spec["destination"] == "/scope/run8_dataset"
    # A linked input shows what its source is set up to write.
    assert jobs[1].spec["source"] == "/scope/run8"
    assert jobs[2].spec["opts"]["raw"] == "/scope/run8_dataset"
    assert [[(link.source, link.target) for link in links] for _job, links in planned] == [
        [], [("W", "source")], [("C", "opts.raw")]]
    assert jobs[1].label == "Convert ND2 — run8 → run8_dataset"
    assert all(j.state == JobState.PENDING for j in jobs)


def test_the_applied_chain_runs_in_order_in_a_real_queue():
    recipe = recipe_from_chain("r", _chain(), "w")
    q = JobQueue()
    ids = iter(["W", "C", "A"])
    for job, links in instantiate(recipe, "/scope/run8", lambda: next(ids)):
        q.add(job)
        for link in links:
            q.set_link(job.id, link.target, link.source, kind=link.kind)
    q.start()
    assert q.next_runnable().id == "W"
    q.mark_running("W")
    q.mark_finished("W", ok=True, produced={"dataset_root": "/scope/run8"})
    assert q.next_runnable().id == "C"
    assert q.materialise("C")["source"] == "/scope/run8"


def test_a_name_is_replaced_only_as_a_whole_token():
    jobs = _chain(root="/scope/run7")
    jobs[1].spec["destination"] = "/scope/run77_and_run7"
    templated = recipe_from_chain("r", jobs, "w").steps[1].spec["destination"]
    assert templated == "{source_parent}/run77_and_{source_name}"


def test_a_file_s_name_loses_its_extension_and_a_folder_s_keeps_its_dots():
    assert source_name("/s/run7.ome.tif") == "run7"
    assert source_name("/s/run7.nd2") == "run7"
    assert source_name("/s/run_2026.11.26") == "run_2026.11.26"


def test_a_chain_with_two_starting_jobs_is_refused():
    q = JobQueue()
    q.add(JobSpec(id="a", kind="ome_import", label="a",
                  spec={"source": "/s/a.ome.tif", "destination": "/d/a"}))
    q.add(JobSpec(id="b", kind="ome_import", label="b",
                  spec={"source": "/s/b.ome.tif", "destination": "/d/b"}))
    q.add(JobSpec(id="c", kind="concatenate", label="c",
                  spec={"dataset1": "/d/a", "dataset2": "/d/b", "destination": "/d/c"}))
    q.set_link("c", "dataset1", "a", kind=CHAIN_KIND)
    q.set_link("c", "dataset2", "b", kind=CHAIN_KIND)
    with pytest.raises(RecipeError, match="starts from 2 jobs"):
        recipe_from_chain("r", q.jobs, "c")


def test_a_chain_whose_input_is_not_set_is_refused():
    jobs = [JobSpec(id="w", kind="watch_folder", label="Watch folder", spec={"folder": ""})]
    with pytest.raises(RecipeError, match="has no input set"):
        recipe_from_chain("r", jobs, "w")


def test_a_damaged_recipe_is_refused_rather_than_half_queued():
    recipe = recipe_from_chain("r", _chain(), "w")
    recipe.steps[1].links[0].step = 2
    with pytest.raises(RecipeError, match="damaged"):
        instantiate(recipe, "/scope/run8", iter("xyz").__next__)


def test_which_input_a_recipe_asks_for():
    assert recipe_from_chain("r", _chain(), "w").wants_file() is False
    jobs = [JobSpec(id="c", kind="nd2_import", label="c",
                    spec={"source": "/s/a.nd2", "destination": "/d/a"})]
    assert recipe_from_chain("r", jobs, "c").wants_file() is True


# ── persistence ───────────────────────────────────────────────────────────────

def test_recipes_round_trip_through_their_file(tmp_path):
    recipe = recipe_from_chain("Nightly", _chain(), "w")
    save_recipes([recipe], tmp_path)
    loaded = load_recipes(tmp_path)
    assert [r.to_json() for r in loaded] == [recipe.to_json()]


def test_a_malformed_file_costs_only_the_bad_entry(tmp_path):
    good = recipe_from_chain("Good", _chain(), "w").to_json()
    recipes_path(tmp_path).write_text(json.dumps({"recipes": [{"steps": []}, good]}))
    assert [r.name for r in load_recipes(tmp_path)] == ["Good"]
    recipes_path(tmp_path).write_text("{not json")
    assert load_recipes(tmp_path) == []


# ── the page, through the real Datasets tab ───────────────────────────────────

@pytest.fixture
def tab(qtbot, tmp_path):
    from datasets.tab import DatasetsTab

    t = DatasetsTab(queue_state_dir=tmp_path / "state")
    yield t
    t.shutdown()
    qtbot.wait(50)


def _queue_chain(controller, root: str) -> None:
    bridge = controller.bridge
    for job in _chain(root):
        links = job.links()
        job.set_links([])
        bridge.add_job(job, links)


def test_save_and_apply_from_the_job_queue_page(tab, tmp_path, monkeypatch):
    controller = tab._controllers["job_queue"]
    notes: list[tuple[str, str]] = []
    monkeypatch.setattr(controller, "_notify", lambda text, kind="warn": notes.append((kind, text)))
    _queue_chain(controller, str(tmp_path / "run7"))

    row = controller.page._rows["a"]
    action = next(a for a in row.build_menu().actions() if a.text() == "Save chain as recipe…")
    monkeypatch.setattr(controller.recipes, "_ask_name", lambda suggestion: "Nightly")
    action.trigger()
    assert notes[-1][0] == "success", notes
    assert [r.name for r in load_recipes(tmp_path / "state")] == ["Nightly"]

    menu = controller.page.build_recipes_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Nightly")
    apply = next(a for a in sub.actions() if a.text() == "Apply to…")
    run8 = tmp_path / "run8"
    monkeypatch.setattr(controller.recipes, "_ask_input", lambda recipe: str(run8))
    apply.trigger()

    jobs = controller.bridge.queue.jobs
    assert len(jobs) == 6
    new = jobs[3:]
    assert [j.kind for j in new] == ["watch_folder", "nd2_import", "analyze"]
    assert new[0].spec["folder"] == str(run8)
    assert [link.source for link in new[1].links()] == [new[0].id]
    assert [link.source for link in new[2].links()] == [new[1].id]
    assert new[1].spec["destination"] == str(tmp_path / "run8_dataset")
    assert notes[-1][0] == "success" and "Queued 3 job(s)" in notes[-1][1]


def test_the_recipes_menu_explains_itself_when_empty(tab):
    controller = tab._controllers["job_queue"]
    actions = controller.page.build_recipes_menu().actions()
    assert len(actions) == 1 and not actions[0].isEnabled()
    assert "Save chain as recipe" in actions[0].text()


def test_deleting_a_recipe_asks_first(tab, tmp_path, monkeypatch):
    controller = tab._controllers["job_queue"]
    monkeypatch.setattr(controller, "_notify", lambda *a, **k: None)
    save_recipes([recipe_from_chain("Old", _chain(), "w")], tmp_path / "state")
    controller.recipes._recipes = load_recipes(tmp_path / "state")
    monkeypatch.setattr(controller.recipes, "_confirm_delete", lambda name: False)
    controller.page.deleteRecipeRequested.emit("Old")
    assert [r.name for r in load_recipes(tmp_path / "state")] == ["Old"]
    monkeypatch.setattr(controller.recipes, "_confirm_delete", lambda name: True)
    controller.page.deleteRecipeRequested.emit("Old")
    assert load_recipes(tmp_path / "state") == []


def test_a_converter_row_offers_only_watch_jobs_as_its_input(tab, tmp_path):
    controller = tab._controllers["job_queue"]
    bridge = controller.bridge
    bridge.add_job(JobSpec(id="v", kind="validate", label="Validate x",
                           spec={"dataset": "/d/x"}))
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="Watch folder — run",
                           spec={"folder": str(tmp_path)}))
    bridge.add_job(JobSpec(id="c", kind="nd2_import", label="conv",
                           spec={"source": "/s/a.nd2", "destination": "/d/a"}))
    menu = controller.page._rows["c"].build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Input from")
    offered = [a.text() for a in sub.actions() if a.text()]
    assert any("Watch folder — run" in t for t in offered)
    assert not any("Validate x" in t for t in offered)
    choice = next(a for a in sub.actions() if "Watch folder — run" in a.text())
    choice.trigger()
    assert [(link.source, link.target) for link in bridge.queue.get("c").links()] == [
        ("w", "source")]


def test_editing_a_watch_linked_conversion_keeps_its_link(tab, tmp_path, monkeypatch):
    """The edit session matches links to inputs by value; a converter's input
    is not a dataset input, and a session that only walked those dropped the
    watch link on Save."""
    controller = tab._controllers["job_queue"]
    monkeypatch.setattr(controller, "_notify", lambda *a, **k: None)
    bridge = controller.bridge
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w",
                           spec={"folder": str(tmp_path)}))
    bridge.add_job(JobSpec(id="c", kind="ome_import", label="c",
                           spec={"source": "/s/a.ome.tif", "destination": "/d/a"}),
                   [ChainLink("w", "source", CHAIN_KIND)])
    assert controller.begin_edit("c")
    assert controller.save_session()
    assert [(link.source, link.target) for link in bridge.queue.get("c").links()] == [
        ("w", "source")]


def test_a_saved_recipe_survives_a_new_session(tab, tmp_path):
    from datasets.job_queue.recipe_actions import RecipeActions

    save_recipes([Recipe(name="Kept", steps=recipe_from_chain("x", _chain(), "w").steps)],
                 tmp_path / "state")
    actions = RecipeActions(tab._controllers["job_queue"].bridge, state_dir=tmp_path / "state",
                            notify=lambda *a: None, parent_widget=lambda: None)
    assert [r.name for r in actions.recipes] == ["Kept"]
