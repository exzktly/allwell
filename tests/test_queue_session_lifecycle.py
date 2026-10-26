"""The life of a queue session around the jobs it adds — second-review findings.

Each test is a path a real user takes through the real ``JobQueueController``,
the real tool controllers and the real ``AnalyzeTab``, where the earlier code
queued a job with the wrong input or rewrote a page it no longer owned, and
said nothing.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from services.job_queue import JobQueue, JobSpec, JobState


def _controller(qtbot, tmp_path):
    from datasets.job_queue.controller import JobQueueController

    jq = JobQueueController(state_dir=tmp_path / "state")
    jq.notices = []
    jq._notify = lambda text, kind="warn": jq.notices.append(text)
    jq.set_session_hooks(lambda _kind, _message: True, lambda _text: None)
    qtbot.addWidget(jq.page)
    return jq


@pytest.fixture
def analyze_queue(qtbot, tmp_path):
    from analyze.tab import AnalyzeTab

    jq = _controller(qtbot, tmp_path)
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    jq.register("analyze", tab)
    jq.set_analyze_provider(tab.collect_spec, tab.validate_for_queue)
    tab.set_queue_sink(lambda spec: jq.add_job("analyze", spec))
    for job_id in ("subA", "subB"):
        jq.bridge.add_job(JobSpec(id=job_id, kind="subset", label=job_id, spec={
            "source": "/x", "destination": str(tmp_path / f"{job_id}_out")}))
    jq._refresh_page()
    tab._input_edit.setText(str(tmp_path / "my_plate"))
    return jq, tab, tmp_path


def _analyze_jobs(jq):
    return [j for j in jq.bridge.queue.jobs if j.kind == "analyze"]


# ── N1: a second setup while one is open ─────────────────────────────────────

def test_a_second_next_step_shows_and_links_its_own_input(analyze_queue):
    jq, tab, tmp = analyze_queue
    jq.page.nextStepRequested.emit("subA", "analyze", "")
    jq.page.nextStepRequested.emit("subB", "analyze", "")
    assert tab._input_edit.text() == str(tmp / "subB_out"), (
        "closing the first setup restored the form over the second's input")
    assert any("closed without saving" in n for n in jq.notices)
    assert jq.save_session()
    (an,) = _analyze_jobs(jq)
    assert [(link.target, link.source) for link in an.links()] == [("opts.raw", "subB")]
    assert tab._input_edit.text() == str(tmp / "my_plate")


def test_replacing_a_new_job_setup_says_so(qtbot, tmp_path):
    from datasets.validate.controller import ValidateController

    jq = _controller(qtbot, tmp_path)
    v = ValidateController()
    qtbot.addWidget(v.page)
    jq.register("validate", v)
    assert jq.begin_new_job("validate")
    assert jq.begin_new_job("validate")
    assert any("closed without saving" in n for n in jq.notices)
    v.shutdown()


# ── N2: the page's own "+ Queue" during a setup ──────────────────────────────

def test_analyze_s_own_queue_button_keeps_the_setup_s_link(analyze_queue):
    jq, tab, tmp = analyze_queue
    jq.page.nextStepRequested.emit("subA", "analyze", "")
    tab._on_queue_requested()
    (an,) = _analyze_jobs(jq)
    assert [(link.target, link.source) for link in an.links()] == [("opts.raw", "subA")]
    assert jq.session() is None, "the setup stayed open for a second copy"


def test_a_datasets_page_s_queue_button_keeps_the_setup_s_link(qtbot, tmp_path):
    from datasets.validate.controller import ValidateController

    jq = _controller(qtbot, tmp_path)
    v = ValidateController(on_add_to_queue=lambda kind, spec: jq.add_job(kind, spec))
    qtbot.addWidget(v.page)
    jq.register("validate", v)
    jq.bridge.add_job(JobSpec(id="sub", kind="subset", label="sub", spec={
        "source": "/x", "destination": str(tmp_path / "planned")}))
    jq._refresh_page()
    jq.page.nextStepRequested.emit("sub", "validate", "")
    v.page.queueRequested.emit()
    (val,) = [j for j in jq.bridge.queue.jobs if j.kind == "validate"]
    assert val.sources() == ["sub"]
    v.shutdown()


# ── N4: the page is handed back before the job can start ─────────────────────

def test_saving_never_rewrites_the_page_of_the_run_it_started(analyze_queue):
    jq, tab, tmp = analyze_queue
    events: list[tuple] = []
    busy = {"v": False}
    real_hydrate = tab.hydrate_job

    def hydrate(spec):
        events.append(("hydrate", busy["v"]))
        return real_hydrate(spec)

    def start(spec):
        busy["v"] = True
        events.append(("start", spec["opts"]["raw"]))

    tab.hydrate_job = hydrate
    tab.start_from_queue = start
    tab.is_busy = lambda: busy["v"]
    jq.bridge.remove_job("subB")      # nothing else ahead of the new job
    jq.page.nextStepRequested.emit("subA", "analyze", "")
    jq.bridge.queue.mark_running("subA")
    jq.bridge.queue.mark_finished("subA", ok=True, produced={"dataset_root": "/real"})
    jq.bridge.queue.start()
    assert jq.save_session()
    assert ("start", "/real") in events
    assert ("hydrate", True) not in events, events


# ── N5: cancelling an edit of a linked Analyze job ───────────────────────────

def test_cancelling_an_edit_of_a_linked_analyze_job_restores_the_form(analyze_queue):
    jq, tab, tmp = analyze_queue
    tab._nuclear_token_edit.setText("MYNUC")
    before = tab.collect_spec()
    job_spec = dict(before)
    job_spec["opts"] = dict(before["opts"], raw=str(tmp / "subA_out"), nuclear_token="JOBNUC")
    jq.bridge.add_job(JobSpec(id="an", kind="analyze", label="an", spec=job_spec))
    jq.bridge.set_link("an", "opts.raw", "subA")
    assert jq.begin_edit("an")
    assert tab._nuclear_token_edit.text() == "JOBNUC"
    jq.end_session()
    assert tab._input_edit.text() == str(tmp / "my_plate")
    assert tab.collect_spec() == before


# ── N3: unlinking a blocked job's failed input ───────────────────────────────

def _conv(job_id):
    return JobSpec(id=job_id, kind="czi_import", label=job_id, spec={})


def test_unlinking_the_failed_input_releases_the_job():
    q = JobQueue([_conv("a"), _conv("b"),
                  JobSpec(id="c", kind="concatenate", label="c", spec={})])
    q.set_link("c", "dataset1", "a", kind="dataset_root")
    q.set_link("c", "dataset2", "b", kind="dataset_root")
    q.mark_running("a")
    q.mark_finished("a", ok=False, message="boom")
    assert q.get("c").state == JobState.BLOCKED
    q.set_link("c", "dataset1", None)
    assert q.get("c").state == JobState.PENDING
    assert q.get("c").message == ""


def test_unlinking_one_of_two_stopped_inputs_names_the_other():
    q = JobQueue([_conv("a"), _conv("b"),
                  JobSpec(id="c", kind="concatenate", label="c", spec={})])
    q.set_link("c", "dataset1", "a", kind="dataset_root")
    q.set_link("c", "dataset2", "b", kind="dataset_root")
    q.mark_running("a")
    q.mark_finished("a", ok=False, message="boom")
    q.get("b").state = JobState.FAILED
    q.set_link("c", "dataset1", None)
    assert q.get("c").state == JobState.BLOCKED
    assert "predecessor b" in q.get("c").message


# ── N6: an edited Subset's explicit wells over a projection ──────────────────

@pytest.fixture
def subset(qtbot):
    from datasets.subset.controller import SubsetController

    c = SubsetController()
    qtbot.addWidget(c.page)
    yield c
    c.shutdown()


_NOTE = {"source": "⧗ Output of queued job “a”"}


def test_an_edited_subset_keeps_a_well_the_projection_does_not_list(subset, tmp_path):
    subset.show_job({"source": str(tmp_path / "pending"), "destination": "",
                     "wells": ["A02", "C05"], "timepoint_labels": None, "fovs": None})
    subset.set_pending_inputs(_NOTE, {"wells": ["A01", "A02"]})
    assert sorted(subset.collect_spec()["wells"]) == ["A02", "C05"]


def test_an_edited_subset_equal_to_the_projection_stays_explicit(subset, tmp_path):
    subset.show_job({"source": str(tmp_path / "pending"), "destination": "",
                     "wells": ["A01", "A02"], "timepoint_labels": None, "fovs": None})
    subset.set_pending_inputs(_NOTE, {"wells": ["A01", "A02"]})
    assert sorted(subset.collect_spec()["wells"]) == ["A01", "A02"]


# ── N7: removing a source ────────────────────────────────────────────────────

def test_removing_a_finished_source_writes_its_folder_into_the_successor():
    q = JobQueue([_conv("a"), JobSpec(id="v", kind="validate", label="v",
                                      spec={"dataset": "/planned"})])
    q.set_link("v", "dataset", "a", kind="dataset_root")
    q.mark_running("a")
    q.mark_finished("a", ok=True, produced={"dataset_root": "/real"})
    q.remove("a")
    assert q.get("v").spec["dataset"] == "/real"
    assert q.get("v").links() == []


def test_removing_a_pending_source_says_its_successor_lost_its_input(qtbot, tmp_path):
    jq = _controller(qtbot, tmp_path)
    jq.bridge.add_job(_conv("a"))
    jq.bridge.add_job(JobSpec(id="v", kind="validate", label="Validate it",
                              spec={"dataset": "/planned"}))
    jq.bridge.set_link("v", "dataset", "a")
    jq._on_remove("a")
    assert any("Validate it" in n for n in jq.notices)


# ── load repair ──────────────────────────────────────────────────────────────

def test_a_link_to_a_job_not_in_the_file_is_dropped_and_said():
    data = {"jobs": [{"id": "c", "kind": "concatenate", "label": "c", "spec": {},
                      "extra_links": [{"source": "ghost", "target": "dataset2",
                                       "kind": "dataset_root"}]}]}
    q = JobQueue.from_json(data)
    assert q.get("c").links() == []
    assert any("ghost" in note for note in q.repair_notes)
