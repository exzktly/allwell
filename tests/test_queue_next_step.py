"""Setting up the next job against an output that is not written yet.

Two CZI scans of one plate are queued for conversion. From the first one's row
the user picks "Queue next step from this output ▸ Concatenate with ▸ <the
second>", and the Concatenate page opens with both datasets filled in from
folders that do not exist yet. Saving it queues a Concatenate whose two inputs
are linked to the two conversions.

Driven through the real ``JobQueueController`` and the real tool controllers
and pages — the seams a user goes through (CLAUDE.md §7.6, §7.8 #3) — with only
``start_from_queue`` replaced where a test must not launch a real worker.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from services.job_kinds import planned_output
from services.job_queue import JobSpec


# ── planned_output ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind, spec, expected", [
    ("czi_import", {"source": "/s.czi", "destination": "/d"}, "/d"),
    ("nd2_import", {"destination": "/d"}, "/d"),
    ("ome_import", {"destination": "/d"}, "/d"),
    ("subset", {"source": "/s", "destination": "/d"}, "/d"),
    ("merge_plates", {"dataset1": "/a", "destination": "/d"}, "/d"),
    ("concatenate", {"dataset1": "/a", "destination": "/d"}, "/d"),
    ("concatenate", {"dataset1": "/a", "destination": "/d", "in_place": True}, "/a"),
    ("import_loose", {"source": "/s", "destination": "/d"}, "/d"),
    ("import_loose", {"source": "/s", "destination": "/d", "in_place": True}, "/s"),
    ("validate", {"dataset": "/v"}, "/v"),
    ("analyze", {"opts": {"raw": "/r"}}, "/r"),
])
def test_planned_output_names_the_folder_each_kind_writes(kind, spec, expected):
    assert planned_output(kind, spec) == expected


def test_planned_output_is_none_when_the_spec_names_nothing():
    assert planned_output("subset", {"source": "/s"}) is None


# ── PathField's pending mode ─────────────────────────────────────────────────

def test_a_pending_path_is_valid_and_says_why_it_is_missing(qtbot, tmp_path):
    from widgets.path_field import PathField, folder_picker

    def must_exist(path):
        return "" if path.exists() else "The folder doesn't exist."

    field = PathField("Dataset", pick=folder_picker("x"), validator=must_exist)
    qtbot.addWidget(field)
    missing = str(tmp_path / "not_yet")
    field.set_path(missing)
    assert not field.is_valid()
    field.set_pending(missing, "⧗ Output of queued job “A”")
    assert field.is_valid() and field.is_pending()
    assert field._hint.text() == "⧗ Output of queued job “A”"
    field.set_path(missing)          # what a Browse does
    assert not field.is_pending() and not field.is_valid()


# ── the whole flow, through the real controllers ─────────────────────────────

@pytest.fixture
def queue(qtbot, tmp_path):
    from datasets.concatenate.controller import ConcatenateController
    from datasets.job_queue.controller import JobQueueController
    from datasets.merge_plates.controller import MergePlatesController
    from datasets.subset.controller import SubsetController
    from datasets.validate.controller import ValidateController

    jq = JobQueueController(state_dir=tmp_path / "state")
    notices: list[str] = []
    jq._notify = lambda text, kind="warn": notices.append(text)
    jq.set_session_hooks(lambda _kind, _message: True, lambda _text: None)
    tools = {
        "concatenate": ConcatenateController(),
        "merge_plates": MergePlatesController(),
        "subset": SubsetController(),
        "validate": ValidateController(),
    }
    for kind, tool in tools.items():
        qtbot.addWidget(tool.page)
        jq.register(kind, tool)
    qtbot.addWidget(jq.page)
    for job_id in ("day1", "day2"):
        jq.bridge.add_job(JobSpec(
            id=job_id, kind="czi_import", label=f"Convert CZI — {job_id}",
            spec={"source": f"/scans/{job_id}.czi",
                  "destination": str(tmp_path / f"plate_{job_id}"),
                  "planned_wells": ["A1", "A2", "B1"]}))
    jq.tools, jq.notices, jq.tmp = tools, notices, tmp_path
    yield jq
    for tool in tools.values():
        tool.shutdown()


def _new_job(jq, kind):
    return next(j for j in jq.bridge.queue.jobs if j.kind == kind)


def _row_menu_action(jq, job_id, *path):
    """Trigger a row menu entry by its labels, the way a click would."""
    menu = jq.page._rows[job_id].build_menu()
    for label in path:
        action = next(a for a in menu.actions() if a.text() == label)
        if action.menu() is not None:
            menu = action.menu()
        else:
            action.trigger()
            return
    raise AssertionError(f"{path} ends on a submenu")


def test_concatenate_with_fills_both_inputs_as_pending(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Concatenate with", "Convert CZI — day2")
    page = jq.tools["concatenate"].page
    assert page.dataset1_path() == str(jq.tmp / "plate_day1")
    assert page.dataset2_path() == str(jq.tmp / "plate_day2")
    assert page._ds1_field.is_pending() and page._ds2_field.is_pending()
    assert "Convert CZI — day1" in page._ds1_field.pending_note()
    assert page._ds1_field.is_valid() and page._ds2_field.is_valid()
    # With a destination the page is otherwise ready, so only the pending
    # inputs can be what keeps Run off. Set the way a Browse sets it, which
    # is what re-checks the page's buttons.
    page._dest_field.set_path(str(jq.tmp / "combined"))
    page._dest_field.changed.emit()
    assert page._queue_btn.isEnabled()
    assert not page._run_btn.isEnabled(), "Run would read folders that are not there"
    assert not page.gap_preview().error
    assert jq.session() == ("concatenate", None)


def test_saving_queues_the_concatenate_with_both_links(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Concatenate with", "Convert CZI — day2")
    jq.tools["concatenate"].page.set_destination_path(str(jq.tmp / "combined"))
    assert jq.save_session()
    cat = _new_job(jq, "concatenate")
    assert [(link.target, link.source) for link in cat.links()] == [
        ("dataset1", "day1"), ("dataset2", "day2")]
    # The page goes back to being an ordinary page once the session ends.
    assert not jq.tools["concatenate"].page.has_pending_inputs()


def test_swapping_the_datasets_swaps_the_links(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Concatenate with", "Convert CZI — day2")
    page = jq.tools["concatenate"].page
    page.set_destination_path(str(jq.tmp / "combined"))
    page._swap_btn.click()
    assert page.dataset1_path() == str(jq.tmp / "plate_day2")
    assert "day2" in page._ds1_field.pending_note()
    assert jq.save_session()
    cat = _new_job(jq, "concatenate")
    assert [(link.target, link.source) for link in cat.links()] == [
        ("dataset1", "day2"), ("dataset2", "day1")]


def test_browsing_a_real_folder_drops_that_input_s_link(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Concatenate with", "Convert CZI — day2")
    page = jq.tools["concatenate"].page
    page.set_destination_path(str(jq.tmp / "combined"))
    page.set_dataset2_path(str(jq.tmp / "some_other_plate"))
    assert jq.save_session()
    cat = _new_job(jq, "concatenate")
    assert [(link.target, link.source) for link in cat.links()] == [("dataset1", "day1")]


def test_a_one_input_next_step_links_its_input(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output", "Validate…")
    page = jq.tools["validate"].page
    assert page._dataset_field.is_pending()
    assert not page._run_btn.isEnabled()
    assert jq.save_session()
    v = _new_job(jq, "validate")
    assert [(link.target, link.source) for link in v.links()] == [("dataset", "day1")]


def test_the_links_are_in_place_before_a_running_queue_can_start_the_job(queue):
    """A running, idle queue starts a newly added job at once. Added first and
    linked after, the job would start against the planned paths on its page.

    The conversions finish while the page is open, and report folders other
    than the ones they were set up with — so the planned paths on the page
    and the real ones the links carry differ, and only the links are right.
    """
    jq = queue
    started: list[dict] = []
    jq.tools["concatenate"].start_from_queue = started.append
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Concatenate with", "Convert CZI — day2")
    page = jq.tools["concatenate"].page
    page.set_destination_path(str(jq.tmp / "combined"))
    for job_id in ("day1", "day2"):
        jq.bridge.queue.mark_running(job_id)
        jq.bridge.queue.mark_finished(job_id, ok=True,
                                      produced={"dataset_root": f"/real/{job_id}"})
    jq.bridge.start()
    assert jq.save_session()
    assert len(started) == 1
    assert (started[0]["dataset1"], started[0]["dataset2"]) == ("/real/day1", "/real/day2")


def test_the_row_links_one_input_from_its_own_menu(queue):
    jq = queue
    jq.bridge.add_job(JobSpec(id="cat", kind="concatenate", label="cat",
                              spec={"dataset1": "/x", "dataset2": "/y"}))
    jq._refresh_page()
    _row_menu_action(jq, "cat", "Dataset 2 from", "Convert CZI — day2")
    _row_menu_action(jq, "cat", "Dataset 1 from", "Convert CZI — day1")
    cat = jq.bridge.queue.get("cat")
    assert [(link.target, link.source) for link in cat.links()] == [
        ("dataset2", "day2"), ("dataset1", "day1")]
    jq._refresh_page()
    detail = jq.page._rows["cat"]._detail_text
    assert "dataset 1 from “Convert CZI — day1”" in detail
    assert "dataset 2 from “Convert CZI — day2”" in detail
    _row_menu_action(jq, "cat", "Dataset 1 from", "Nothing (use the folder on its page)")
    assert [link.target for link in jq.bridge.queue.get("cat").links()] == ["dataset2"]


def test_a_failed_job_offers_no_next_step(queue):
    jq = queue
    jq.bridge.queue.mark_running("day1")
    jq.bridge.queue.mark_finished("day1", ok=False, message="boom")
    jq._refresh_page()
    labels = [a.text() for a in jq.page._rows["day1"].build_menu().actions()]
    assert "Queue next step from this output" not in labels


def test_editing_a_linked_job_shows_its_links_and_keeps_them(queue):
    jq = queue
    jq.bridge.add_job(JobSpec(id="cat", kind="concatenate", label="cat", spec={
        "dataset1": str(jq.tmp / "plate_day1"), "dataset2": str(jq.tmp / "plate_day2"),
        "destination": str(jq.tmp / "combined")}))
    jq.bridge.set_link("cat", "dataset1", "day1")
    jq.bridge.set_link("cat", "dataset2", "day2")
    assert jq.begin_edit("cat")
    page = jq.tools["concatenate"].page
    assert page._ds1_field.is_pending() and page._ds2_field.is_pending()
    page.set_dataset2_path(str(jq.tmp / "elsewhere"))
    assert jq.save_session()
    cat = jq.bridge.queue.get("cat")
    assert [(link.target, link.source) for link in cat.links()] == [("dataset1", "day1")]


def test_a_subset_next_step_offers_the_wells_the_conversion_mapped(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output", "Subset…")
    page = jq.tools["subset"].page
    assert page._src_field.is_pending()
    assert sorted(page.selected_wells()) == ["A01", "A02", "B01"]
    page.set_selected_wells(["A02"])
    page.set_destination_path(str(jq.tmp / "sub"))
    assert jq.save_session()
    sub = _new_job(jq, "subset")
    assert sub.spec["wells"] == ["A02"]
    assert [(link.target, link.source) for link in sub.links()] == [("source", "day1")]


def test_merge_plates_with_marks_both_inputs_pending(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Merge plates with", "Convert CZI — day2")
    page = jq.tools["merge_plates"].page
    assert page.has_pending_inputs()
    page._dest_field.set_path(str(jq.tmp / "merged"))
    page._dest_field.changed.emit()
    assert page._queue_btn.isEnabled()
    assert not page._run_btn.isEnabled()
    assert not page.dashboard.isVisibleTo(page)


# ── review findings R7 / R8a ─────────────────────────────────────────────────

def test_removing_a_source_closes_its_next_step_setup(queue):
    jq = queue
    _row_menu_action(jq, "day1", "Queue next step from this output",
                     "Concatenate with", "Convert CZI — day2")
    jq.bridge.remove_job("day2")
    assert jq.session() is None
    assert any("removed" in n for n in jq.notices)
    assert not any(j.kind == "concatenate" for j in jq.bridge.queue.jobs)


def test_unlinking_an_input_lets_a_started_queue_run_the_job(queue):
    jq = queue
    started: list[dict] = []
    jq.tools["validate"].start_from_queue = started.append
    for job_id in ("day1", "day2"):
        jq.bridge.remove_job(job_id)
    jq.bridge.add_job(JobSpec(id="a", kind="subset", label="a", spec={}))
    jq.bridge.add_job(JobSpec(id="v", kind="validate", label="v",
                              spec={"dataset": "/somewhere"}))
    jq.tools["subset"].start_from_queue = lambda spec: None
    jq.bridge.set_link("v", "dataset", "a")
    jq.bridge.queue.mark_running("a")    # 'a' is busy elsewhere, so v waits
    jq.bridge._current = None
    jq.bridge.queue.start()
    jq.bridge.set_link("v", "dataset", "")
    assert len(started) == 1 and started[0]["dataset"] == "/somewhere"
