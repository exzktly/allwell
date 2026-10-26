"""The second UX review of the Job Queue's chained inputs, one test per finding.

Checked where the user meets each one — the row, the menu, the field — and
through the real ``DatasetsTab`` wiring for the input-field picker.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from services.job_queue import JobQueue, JobSpec, JobState


def _conv(job_id, label=None, **kw):
    return JobSpec(id=job_id, kind="czi_import", label=label or job_id,
                   spec={"destination": f"/planned/{job_id}"}, **kw)


def _an(job_id="an", label="Analyze"):
    return JobSpec(id=job_id, kind="analyze", label=label, spec={"opts": {"raw": "/planned/a"}})


def _linked():
    q = JobQueue([_conv("a", "Convert"), _an()])
    q.set_link("an", "opts.raw", "a", kind="dataset_root")
    return q


def _succeed(q, job_id, root=None):
    q.mark_running(job_id)
    q.mark_finished(job_id, ok=True, produced={"dataset_root": root or f"/real/{job_id}"})


def _fail(q, job_id):
    q.mark_running(job_id)
    q.mark_finished(job_id, ok=False, message="truncated")


# ── K1: Duplicate keeps the links ────────────────────────────────────────────

def test_k1_a_copy_of_a_linked_job_waits_like_the_original():
    q = _linked()
    copy = q.duplicate("an", "an2")
    assert copy.sources() == ["a"]
    _fail(q, "a")
    assert q.get("an").state == JobState.BLOCKED
    assert q.get("an2").state == JobState.BLOCKED, "the copy would run on the planned folder"
    assert q.next_runnable() is None


def test_k1_a_copy_made_after_the_source_failed_is_blocked_at_once():
    q = _linked()
    _fail(q, "a")
    assert q.duplicate("an", "an2").state == JobState.BLOCKED


def test_k1_a_copy_runs_on_the_real_output():
    q = _linked()
    q.duplicate("an", "an2")
    _succeed(q, "a")
    assert q.materialise("an2")["opts"]["raw"] == "/real/a"


# ── K2: Retry and the jobs that used the output ──────────────────────────────

def test_k2_retrying_with_downstream_runs_the_chain_again():
    q = _linked()
    _succeed(q, "a")
    _succeed(q, "an")
    assert q.retry("a", with_downstream=True)
    assert q.get("an").state == JobState.PENDING
    assert q.get("an").dataset_root is None


def test_k2_retrying_alone_marks_the_rest_out_of_date():
    q = _linked()
    _succeed(q, "a")
    _succeed(q, "an")
    assert q.retry("a")
    an = q.get("an")
    assert an.state == JobState.SUCCEEDED
    assert an.stale_because == "Convert", "reported as a current result of a conversion re-run since"
    assert JobQueue.from_json(q.to_json()).get("an").stale_because == "Convert"
    _succeed(q, "a")
    q.retry("an")
    assert q.get("an").stale_because == ""


def test_k2_the_row_says_out_of_date_and_offers_both_retries(qtbot):
    from datasets.job_queue.row import JobRow

    q = _linked()
    _succeed(q, "a")
    _succeed(q, "an")
    q.retry("a")
    row = JobRow(q.get("an"))
    qtbot.addWidget(row)
    row.update_from(q.get("an"))
    assert row._detail_text.startswith("Out of date — “Convert” was run again")
    source = JobRow(q.get("a"))
    qtbot.addWidget(source)
    q.get("a").state = JobState.SUCCEEDED
    source.update_from(q.get("a"), finished_downstream=1)
    menu = source.build_menu()
    retry = next(a for a in menu.actions() if a.text() == "Retry")
    labels = [a.text() for a in retry.menu().actions()]
    assert labels == ["This job and the 1 job(s) that use its output",
                      "This job only — mark those out of date"]
    seen = []
    source.retryClicked.connect(lambda job_id, chain: seen.append((job_id, chain)))
    retry.menu().actions()[0].trigger()
    assert seen == [("a", True)]


# ── K3: Clear completed keeps a failure others wait on ───────────────────────

def test_k3_clear_completed_keeps_a_failed_job_others_wait_on():
    q = JobQueue([_conv("a"), _an(), _conv("z", state=JobState.FAILED)])
    q.set_link("an", "opts.raw", "a", kind="dataset_root")
    _fail(q, "a")
    q.clear_terminal()
    assert [j.id for j in q.jobs] == ["a", "an"], "the unrelated failure goes, the blocking one stays"
    assert q.retry("a")
    assert q.get("an").state == JobState.PENDING


# ── K4 / L3: a source queued below, and a reason when there is none ──────────

def test_k4_choosing_a_job_below_moves_this_one_under_it(qtbot, tmp_path):
    from datasets.job_queue.controller import JobQueueController

    jq = JobQueueController(state_dir=tmp_path)
    qtbot.addWidget(jq.page)
    jq.bridge.add_job(_an())
    jq.bridge.add_job(_conv("a", "Re-added conversion"))
    jq._refresh_page()
    menu = jq.page._rows["an"].build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Input from")
    entry = next(a for a in sub.actions() if "Re-added conversion" in a.text())
    assert "(moves this job below it)" in entry.text()
    entry.trigger()
    assert [j.id for j in jq.bridge.queue.jobs] == ["a", "an"]
    assert jq.bridge.queue.get("an").sources() == ["a"]


def test_l3_an_input_menu_with_nothing_to_offer_says_why(qtbot):
    from datasets.job_queue.row import JobRow

    row = JobRow(_an())
    qtbot.addWidget(row)
    menu = row.build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Input from")
    texts = [a.text() for a in sub.actions() if a.text()]
    assert any(t.startswith("No other queued job can be its input") for t in texts)


# ── L1: one number per job ───────────────────────────────────────────────────

def test_l1_rows_and_menus_number_jobs_the_same_way(qtbot):
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    qtbot.addWidget(page)
    jobs = [_conv("a", "Day 1", state=JobState.SUCCEEDED), _conv("b", "Day 2"),
            JobSpec(id="v", kind="validate", label="Validate", spec={})]
    page.set_jobs(jobs)
    assert page._rows["b"]._ordinal.text() == "2."
    menu = page._rows["v"].build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Input from")
    assert "2. Day 2" in [a.text() for a in sub.actions()]


# ── L2: the root cause is named ──────────────────────────────────────────────

def test_l2_a_job_two_levels_down_names_the_job_that_failed():
    q = JobQueue([_conv("a", "Convert day2"),
                  JobSpec(id="cat", kind="concatenate", label="Concatenate", spec={}),
                  _an()])
    q.set_link("cat", "dataset1", "a", kind="dataset_root")
    q.set_link("an", "opts.raw", "cat", kind="dataset_root")
    _fail(q, "a")
    message = q.get("an").message
    assert "“Concatenate”" in message and "“Convert day2” failed" in message
    assert "blocked itself" not in message


# ── L4 / L5: the next-step menu ──────────────────────────────────────────────

def test_l4_an_analysis_is_not_offered_analysis_of_itself(qtbot):
    from datasets.job_queue.row import NEXT_STEP_MENU, JobRow

    row = JobRow(_an())
    qtbot.addWidget(row)
    menu = row.build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == NEXT_STEP_MENU)
    labels = [a.text() for a in sub.actions() if a.text()]
    assert "Analyze…" not in labels and "Validate…" in labels


def test_l4_concatenate_with_leaves_out_this_job_s_own_lineage(qtbot):
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    qtbot.addWidget(page)
    q = _linked()
    q.add(_conv("b", "Other plate"))
    page.set_jobs(q.jobs)
    others = [label for _id, label in page._rows["an"]._others]
    assert "Convert" not in others, "offered concatenating an analysis with its own source"
    assert "Other plate" in others


def test_l5_the_next_step_tools_come_from_the_declared_inputs():
    from datasets.job_queue.row import NEXT_STEP_KINDS, PAIR_STEP_KINDS
    from services.job_kinds import INPUT_TARGETS

    assert set(NEXT_STEP_KINDS) | set(PAIR_STEP_KINDS) == set(INPUT_TARGETS)
    assert set(PAIR_STEP_KINDS) == {"concatenate", "merge_plates"}


# ── P1: "Output of a queued job ▸" on the tool page's own input ──────────────

@pytest.fixture
def tab(qtbot, tmp_path):
    from analyze.tab import AnalyzeTab
    from datasets.tab import DatasetsTab

    analyze = AnalyzeTab()
    qtbot.addWidget(analyze)
    t = DatasetsTab(analyze_controller=analyze, queue_state_dir=tmp_path,
                    on_reveal_analyze=lambda: True)
    qtbot.addWidget(t)
    jq = t._controllers["job_queue"]
    jq.bridge.add_job(_conv("day1", "Convert day1"))
    jq.bridge.add_job(_conv("day2", "Convert day2"))
    t.analyze = analyze
    yield t
    t.shutdown()


def _pick(field, label):
    menu, sub = field._alternative_menu
    sub.aboutToShow.emit()
    next(a for a in sub.actions() if a.text().endswith(label)).trigger()


def test_p1_a_datasets_page_takes_a_queued_output_from_its_own_field(tab):
    jq = tab._controllers["job_queue"]
    page = tab._controllers["concatenate"].page
    _pick(page._ds1_field, "Convert day1")
    _pick(page._ds2_field, "Convert day2")
    assert page._ds1_field.is_pending() and page._ds2_field.is_pending()
    assert page.dataset1_path() == "/planned/day1"
    assert jq.session() == ("concatenate", None)
    page._dest_field.set_path("/out/combined")
    assert jq.save_session()
    cat = next(j for j in jq.bridge.queue.jobs if j.kind == "concatenate")
    assert [(link.target, link.source) for link in cat.links()] == [
        ("dataset1", "day1"), ("dataset2", "day2")]


def test_p1_analyze_takes_a_queued_output_from_beside_browse(tab):
    jq = tab._controllers["job_queue"]
    button, _menu, sub, fill = tab.analyze._queued_output_menu
    fill()
    next(a for a in sub.actions() if a.text().endswith("Convert day1")).trigger()
    assert tab.analyze._input_edit.text() == "/planned/day1"
    assert tab.analyze.has_pending_inputs()
    assert jq.save_session()
    an = next(j for j in jq.bridge.queue.jobs if j.kind == "analyze")
    assert an.sources() == ["day1"]


def test_p1_the_menu_says_so_when_no_job_can_be_the_input(qtbot, tmp_path):
    from datasets.tab import QUEUED_OUTPUT_EMPTY, DatasetsTab

    t = DatasetsTab(analyze_controller=None, queue_state_dir=tmp_path)
    qtbot.addWidget(t)
    field = t._controllers["validate"].page._dataset_field
    _menu, sub = field._alternative_menu
    sub.aboutToShow.emit()
    actions = [a for a in sub.actions() if a.text()]
    assert [a.text() for a in actions] == [QUEUED_OUTPUT_EMPTY]
    assert not actions[0].isEnabled()
    t.shutdown()


def test_p1_a_job_whose_output_is_never_coming_is_not_offered(tab):
    """A failed conversion writes nothing; offering it as an input would
    queue a job blocked the moment it is saved. "3." is the job's place."""
    jq = tab._controllers["job_queue"]
    jq.bridge.add_job(_conv("day3", "Convert day3"))
    _fail(jq.bridge.queue, "day2")
    assert jq.queued_outputs("validate") == [
        ("day1", "1. Convert day1"), ("day3", "3. Convert day3")]
