"""Pickers built from what a queued job will write, and the run-time check.

A Subset set up after a queued job cannot read its source — it is not there
yet — so its well, timepoint and FOV pickers are built from a projection of
what that job is expected to write (``datasets.job_queue.projection``). An
Analyze set up the same way takes the converter's recorded channel roles as
its starting point. When the job finally runs, a Subset that names something
its real source lacks is refused, naming it, rather than quietly keeping less.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from datasets.job_queue.projection import Projection, on_disk, project
from services.job_kinds import produced_layout
from services.job_queue import JobQueue, JobSpec
from tests.fixtures.dataset_builder import build_dataset

_ROLES = [["DAPI", "nuclear"], ["GFP", "fluor"]]


def _czi(job_id, wells, roles=_ROLES):
    spec = {"source": f"/{job_id}.czi", "destination": f"/data/{job_id}",
            "channel_roles": roles}
    if wells is not None:
        spec["planned_wells"] = wells
    return JobSpec(id=job_id, kind="czi_import", label=job_id, spec=spec)


def _q(*jobs):
    return JobQueue(list(jobs))


# ── the projection itself ────────────────────────────────────────────────────

def test_a_converter_projects_the_wells_its_page_mapped_and_nothing_else():
    p = project(_q(_czi("a", ["A1", "B02"])), "a")
    assert p.wells == ("A01", "B02")
    assert p.timepoints is None and p.fovs is None, (
        "a converted plate has no pipeline_info, so its timepoints are unknown, not empty")
    assert p.layout == produced_layout("czi_import", {"channel_roles": _ROLES})


def test_a_converter_whose_page_never_read_the_plate_projects_unknown_wells():
    assert project(_q(_czi("a", None)), "a").wells is None


def test_concatenate_projects_the_union_of_its_two_inputs():
    q = _q(_czi("a", ["A01", "A02"]), _czi("b", ["A02", "B01"]),
           JobSpec(id="cat", kind="concatenate", label="cat", spec={}))
    q.set_link("cat", "dataset1", "a", kind="dataset_root")
    q.set_link("cat", "dataset2", "b", kind="dataset_root")
    p = project(q, "cat")
    assert p.wells == ("A01", "A02", "B01")
    assert p.timepoints is None
    assert p.layout == produced_layout("czi_import", {"channel_roles": _ROLES})


def test_concatenate_with_an_unknown_input_projects_unknown_wells():
    q = _q(_czi("a", ["A01"]), _czi("b", None),
           JobSpec(id="cat", kind="concatenate", label="cat", spec={}))
    q.set_link("cat", "dataset1", "a", kind="dataset_root")
    q.set_link("cat", "dataset2", "b", kind="dataset_root")
    assert project(q, "cat").wells is None


def test_merge_plates_moves_dataset_2_s_wells_as_the_job_will():
    q = _q(_czi("a", ["A01", "A02"]), _czi("b", ["A01", "A02"]),
           JobSpec(id="m", kind="merge_plates", label="m",
                   spec={"remap_enabled": True, "remap_origin": "E01"}))
    q.set_link("m", "dataset1", "a", kind="dataset_root")
    q.set_link("m", "dataset2", "b", kind="dataset_root")
    assert project(q, "m").wells == ("A01", "A02", "E01", "E02")


def test_a_subset_projects_what_it_keeps_of_its_input():
    q = _q(_czi("a", ["A01", "A02", "B01"]),
           JobSpec(id="s", kind="subset", label="s", spec={"wells": ["A02", "C09"]}))
    q.set_link("s", "source", "a", kind="dataset_root")
    assert project(q, "s").wells == ("A02",)


def test_analyze_and_validate_project_their_input(tmp_path):
    q = _q(_czi("a", ["A01"]),
           JobSpec(id="an", kind="analyze", label="an", spec={"opts": {}}),
           JobSpec(id="v", kind="validate", label="v", spec={}))
    q.set_link("an", "opts.raw", "a", kind="dataset_root")
    q.set_link("v", "dataset", "an", kind="dataset_root")
    assert project(q, "v").wells == ("A01",)


def test_a_finished_job_is_read_from_the_folder_it_wrote(tmp_path):
    root = tmp_path / "done"
    build_dataset(root, wells=["C03", "D04"], timepoints=["0h", "24h"], fovs=("F001",))
    q = _q(_czi("a", ["A01"]))
    q.mark_running("a")
    q.mark_finished("a", ok=True, produced={"dataset_root": str(root)})
    p = project(q, "a")
    assert p.wells == ("C03", "D04"), "used the plan instead of what was written"
    assert p.timepoints == ("0h", "24h")


def test_an_input_read_from_a_folder_is_read_from_disk(tmp_path):
    root = tmp_path / "plate"
    build_dataset(root, wells=["A01"], timepoints=["0h"], fovs=("F001", "F002"))
    assert on_disk(root) == Projection(wells=("A01",), timepoints=("0h",),
                                       fovs=("F001", "F002"))
    assert on_disk(tmp_path / "missing") == Projection()


# ── the converter records the wells its page mapped ──────────────────────────

@pytest.fixture
def czi(qtbot):
    from datasets.czi_import.controller import CziImportController

    c = CziImportController()
    qtbot.addWidget(c.page)
    yield c
    c.shutdown()


def test_a_queued_conversion_records_the_page_s_wells(czi):
    czi.page._dashboard.set_wells(["A01", "B02"])
    assert czi.collect_spec()["planned_wells"] == ["A01", "B02"]


def test_an_unread_plate_records_no_wells_rather_than_none(czi):
    assert "planned_wells" not in czi.collect_spec()


def test_editing_a_conversion_keeps_its_wells_before_the_rescan(czi, tmp_path):
    source = str(tmp_path / "scan.czi")
    czi.show_job({"source": source, "destination": str(tmp_path / "out"),
                  "planned_wells": ["C03"]})
    czi.page._dashboard.set_wells([])
    assert czi.collect_spec()["planned_wells"] == ["C03"]


# ── the Subset page over a projection ────────────────────────────────────────

@pytest.fixture
def subset(qtbot):
    from datasets.subset.controller import SubsetController

    c = SubsetController()
    qtbot.addWidget(c.page)
    yield c
    c.shutdown()


_NOTE = {"source": "⧗ Output of queued job “a”"}


def test_the_pickers_offer_the_projected_wells(subset, tmp_path):
    page = subset.page
    page.set_source_path(str(tmp_path / "not_written"))
    subset.set_pending_inputs(_NOTE, {"wells": ["A01", "B02"], "timepoints": None,
                                      "fovs": None})
    assert sorted(page.selected_wells()) == ["A01", "B02"]
    assert not page._wells_hint.isVisibleTo(page)
    assert page.available_timepoints() == []
    assert "known only when it runs" in page._tp_hint.text()
    assert not page._run_btn.isEnabled()


def test_every_projected_well_ticked_queues_as_every_well(subset, tmp_path):
    page = subset.page
    page.set_source_path(str(tmp_path / "not_written"))
    subset.set_pending_inputs(_NOTE, {"wells": ["A01", "B02"]})
    assert subset.collect_spec()["wells"] is None, (
        "a well the job writes that the projection missed would be dropped")
    page.set_selected_wells(["B02"])
    assert subset.collect_spec()["wells"] == ["B02"]


def test_unknown_wells_say_so_and_queue_as_every_well(subset, tmp_path):
    page = subset.page
    page.set_source_path(str(tmp_path / "not_written"))
    subset.set_pending_inputs(_NOTE, {"wells": None})
    assert page._wells_hint.isVisibleTo(page)
    assert subset.collect_spec()["wells"] is None


def test_projected_timepoints_become_checkboxes(subset, tmp_path):
    page = subset.page
    page.set_source_path(str(tmp_path / "not_written"))
    subset.set_pending_inputs(_NOTE, {"wells": ["A01"], "timepoints": ["24h", "0h"],
                                      "fovs": ["F002"]})
    assert page.available_timepoints() == ["0h", "24h"]
    assert page.available_fovs() == ["F002"]


def test_an_edited_job_keeps_its_own_choice_over_the_projection(subset, tmp_path):
    page = subset.page
    subset.show_job({"source": str(tmp_path / "not_written"), "destination": "",
                     "wells": ["B02"], "timepoint_labels": None, "fovs": None})
    subset.set_pending_inputs(_NOTE, {"wells": ["A01", "B02"]})
    assert page.selected_wells() == ["B02"]


# ── the run-time check ───────────────────────────────────────────────────────

def test_missing_from_source_names_what_is_absent():
    from datasets.subset.subset_compute import missing_from_source

    out = missing_from_source(
        wells=["A1", "C09"], timepoints=["0h", "72h"], fovs=None,
        source_wells=["A01", "B02"], source_timepoints=["0h", "24h"],
        source_fovs=["F001"])
    assert out == ["wells C09", "timepoints 72h"]


def test_all_and_unlisted_dimensions_are_not_judged():
    from datasets.subset.subset_compute import missing_from_source

    assert missing_from_source(wells=None, timepoints=["99h"], fovs=["F9"],
                               source_wells=["A01"], source_timepoints=[],
                               source_fovs=[]) == []


def test_a_queued_subset_naming_an_absent_well_fails_and_copies_nothing(subset, tmp_path):
    src = tmp_path / "plate"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"])
    finished: list[tuple] = []
    subset.jobFinished.connect(lambda ok, msg, path: finished.append((ok, msg, path)))
    launched: list[bool] = []
    subset._launch = lambda: launched.append(True)
    subset.start_from_queue({"source": str(src), "destination": str(tmp_path / "out"),
                             "wells": ["A01", "C09"], "timepoint_labels": ["24h"],
                             "fovs": None})
    assert not launched
    (ok, message, _path), = finished
    assert not ok
    assert "wells C09" in message


def test_a_queued_subset_within_its_source_runs(subset, tmp_path):
    src = tmp_path / "plate"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"])
    launched: list[bool] = []
    subset._launch = lambda: launched.append(True)
    subset.start_from_queue({"source": str(src), "destination": str(tmp_path / "out"),
                             "wells": ["B02"], "timepoint_labels": ["24h"], "fovs": None})
    assert launched == [True]


# ── Analyze over a pending input ─────────────────────────────────────────────

@pytest.fixture
def analyze(qtbot):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    return tab


def test_analyze_takes_the_projected_layout_and_detects_at_run_time(analyze, tmp_path):
    layout = produced_layout("czi_import", {"channel_roles": [["DAPI", "nuclear"],
                                                              ["mCherry", "fluor"]]})
    planned = str(tmp_path / "combined")
    analyze._auto_detect_cb.setChecked(False)
    analyze._input_edit.setText(planned)
    analyze.set_pending_inputs({"opts.raw": "⧗ Output of queued job “cat”"},
                               {"layout": layout})
    assert analyze._nuclear_token_edit.text() == "DAPI"
    assert analyze._fluor_tokens_list() == ["mCherry"]
    assert analyze._auto_detect_cb.isChecked()
    assert "queued job “cat”" in analyze._layout_lbl.text()
    assert "doesn't exist" not in analyze._layout_lbl.text()
    spec = analyze.collect_spec()
    assert spec["opts"]["raw"] == planned and spec["auto_detect"]


def test_analyze_forgets_the_pending_input_once_another_folder_is_chosen(analyze, tmp_path):
    analyze._input_edit.setText(str(tmp_path / "combined"))
    analyze.set_pending_inputs({"opts.raw": "⧗ Output of queued job “cat”"}, None)
    analyze._input_edit.setText(str(tmp_path / "elsewhere"))
    assert not analyze.has_pending_inputs()
    assert "queued job" not in analyze._layout_lbl.text()


def test_analyze_queued_after_a_concatenate_of_two_conversions(qtbot, tmp_path):
    """The motivating chain, set up the way a user sets it up: two CZI jobs,
    a Concatenate of their outputs, and "Queue next step ▸ Analyze…" from the
    Concatenate's row. The Analyze form takes the conversions' channel roles
    through the Concatenate, and the job is queued linked to it."""
    from analyze.tab import AnalyzeTab
    from datasets.job_queue.controller import JobQueueController

    jq = JobQueueController(state_dir=tmp_path / "state")
    jq._notify = lambda text, kind="warn": None
    jq.set_session_hooks(lambda _kind, _message: True, lambda _text: None)
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    qtbot.addWidget(jq.page)
    jq.register("analyze", tab)
    jq.set_analyze_provider(tab.collect_spec, tab.validate_for_queue)
    roles = [["Hoechst", "nuclear"], ["GFP", "fluor"]]
    for job_id in ("day1", "day2"):
        jq.bridge.add_job(_czi(job_id, ["A01"], roles))
    jq.bridge.add_job(JobSpec(id="cat", kind="concatenate", label="cat",
                              spec={"destination": str(tmp_path / "combined")}))
    jq.bridge.set_link("cat", "dataset1", "day1")
    jq.bridge.set_link("cat", "dataset2", "day2")
    jq._refresh_page()

    jq.page.nextStepRequested.emit("cat", "analyze", "")
    assert tab._input_edit.text() == str(tmp_path / "combined")
    assert tab.has_pending_inputs()
    assert tab._nuclear_token_edit.text() == "Hoechst"
    assert tab._fluor_tokens_list() == ["GFP"]
    assert jq.save_session()
    an = next(j for j in jq.bridge.queue.jobs if j.kind == "analyze")
    assert [(link.target, link.source) for link in an.links()] == [("opts.raw", "cat")]
    assert an.spec["auto_detect"]
    assert not tab.has_pending_inputs()


# ── review findings R1 / R2: an edit must not widen a Subset ─────────────────

def test_a_no_op_edit_of_an_explicit_subset_keeps_its_wells(qtbot, tmp_path):
    """Regression: editing a Subset with explicit wells and saving unchanged
    saved every well of the source."""
    from datasets.job_queue.controller import JobQueueController
    from datasets.subset.controller import SubsetController

    src = tmp_path / "plate"
    build_dataset(src, wells=["A01", "A02", "B01"], timepoints=["0h", "24h"])
    jq = JobQueueController(state_dir=tmp_path / "state")
    jq._notify = lambda text, kind="warn": None
    jq.set_session_hooks(lambda _kind, _message: True, lambda _text: None)
    sub = SubsetController()
    qtbot.addWidget(sub.page)
    qtbot.addWidget(jq.page)
    jq.register("subset", sub)
    spec = {"source": str(src), "destination": str(tmp_path / "out"),
            "wells": ["A02"], "timepoint_labels": ["24h"], "fovs": None}
    jq.bridge.add_job(JobSpec(id="s", kind="subset", label="s", spec=dict(spec)))
    assert jq.begin_edit("s")
    assert jq.save_session()
    saved = jq.bridge.queue.get("s").spec
    assert saved["wells"] == ["A02"]
    assert saved["timepoint_labels"] == ["24h"]
    sub.shutdown()


def test_an_edited_subset_keeps_explicit_timepoints_the_projection_does_not_know(subset, tmp_path):
    page = subset.page
    subset.show_job({"source": str(tmp_path / "not_written"), "destination": "",
                     "wells": None, "timepoint_labels": ["0h"], "fovs": ["F002"]})
    # A folder that does not exist has no boxes to tick, which is exactly the
    # case: the edit's choices must survive until the pending call offers them.
    assert page.available_timepoints() == []
    subset.set_pending_inputs(_NOTE, {"wells": ["A01"], "timepoints": None, "fovs": None})
    assert page.available_timepoints() == ["0h"]
    spec = subset.collect_spec()
    assert spec["timepoint_labels"] == ["0h"], "an explicit timepoint became 'all'"
    assert spec["fovs"] == ["F002"]
    for cb in page._tp_checks.values():
        cb.setChecked(False)
    assert subset.collect_spec()["timepoint_labels"] is None


# ── review findings R3 / R4 / R5: the Analyze next step ──────────────────────

def _analyze_queue(qtbot, tmp_path, destination):
    from analyze.tab import AnalyzeTab
    from datasets.job_queue.controller import JobQueueController

    jq = JobQueueController(state_dir=tmp_path / "state")
    jq._notify = lambda text, kind="warn": None
    jq.set_session_hooks(lambda _kind, _message: True, lambda _text: None)
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    qtbot.addWidget(jq.page)
    jq.register("analyze", tab)
    jq.set_analyze_provider(tab.collect_spec, tab.validate_for_queue)
    jq.bridge.add_job(JobSpec(id="sub", kind="subset", label="sub",
                              spec={"source": "/x", "destination": destination}))
    jq._refresh_page()
    return jq, tab


def test_a_path_respelled_by_the_form_keeps_its_link(qtbot, tmp_path):
    """Analyze collects its input through ``Path``, which drops a trailing
    separator (and on Windows turns ``/`` into ``\\``). Compared as raw
    strings, the link was dropped and an unlinked job queued."""
    jq, tab = _analyze_queue(qtbot, tmp_path, str(tmp_path / "subset_out") + "/")
    jq.page.nextStepRequested.emit("sub", "analyze", "")
    assert jq.save_session()
    an = next(j for j in jq.bridge.queue.jobs if j.kind == "analyze")
    assert an.spec["opts"]["raw"] == str(tmp_path / "subset_out"), "fixture: the form respelled it"
    assert [(link.target, link.source) for link in an.links()] == [("opts.raw", "sub")]


def test_analyze_refuses_to_run_on_a_pending_input(qtbot, tmp_path):
    """If an earlier run left the planned folder behind, Run would analyse
    that and say nothing."""
    planned = tmp_path / "subset_out"
    build_dataset(planned, wells=["A01"], timepoints=["0h"])
    jq, tab = _analyze_queue(qtbot, tmp_path, str(planned))
    jq.page.nextStepRequested.emit("sub", "analyze", "")
    assert tab.has_pending_inputs()
    assert tab._validate_run_request() is None
    assert tab._validate_run_request(for_queue=True) is not None


def test_ending_an_analyze_next_step_restores_the_user_s_form(qtbot, tmp_path):
    jq, tab = _analyze_queue(qtbot, tmp_path, str(tmp_path / "subset_out"))
    tab._auto_detect_cb.setChecked(False)
    tab._input_edit.setText(str(tmp_path / "my_own_plate"))
    tab._nuclear_token_edit.setText("NIR")
    before = tab.collect_spec()
    jq.page.nextStepRequested.emit("sub", "analyze", "")
    assert tab._auto_detect_cb.isChecked()
    jq.end_session()
    assert not tab._auto_detect_cb.isChecked()
    assert tab._input_edit.text() == str(tmp_path / "my_own_plate")
    assert tab.collect_spec() == before
