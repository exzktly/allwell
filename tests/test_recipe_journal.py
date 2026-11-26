"""A recipe run's journal, and what it lets the queue recover after a crash.

The journal (``services.recipe_journal``) is append-only JSON lines; the tests
check it survives a write cut off mid-line. The bridge tests drive a real
``QueueBridge`` with the real Watch folder controller and record every event a
recipe run goes through — applied, started (with the folder a link really
filled), status, finished — then restart the queue from its file the way the
app does after a crash, and check the watch resumed counts the files written
while the app was down.
"""

from __future__ import annotations

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets.job_queue.controller import QueueBridge
from datasets.job_queue.recipe_actions import RecipeActions
from datasets.watch_folder.controller import WatchFolderController
from datasets.watch_folder.watch_folder_compute import SETTLED, WAITING, SettleTracker
from services import recipe_journal
from services.job_kinds import CHAIN_KIND
from services.job_queue import JobQueue, JobSpec, JobState, save_queue
from services.job_recipes import recipe_from_chain, save_recipes


# ── the file ──────────────────────────────────────────────────────────────────

def test_a_line_cut_off_by_a_crash_costs_only_that_line(tmp_path):
    recipe_journal.append("run1", "applied", state_dir=tmp_path, now=1.0, name="r")
    recipe_journal.append("run1", "started", state_dir=tmp_path, now=2.0, job="a", step=0)
    path = recipe_journal.journal_path("run1", tmp_path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"ts": 3.0, "event": "finis')          # the crash
    events = recipe_journal.read_events("run1", tmp_path)
    assert [e["event"] for e in events] == ["applied", "started"]


def test_a_torn_last_line_then_a_new_event_keeps_the_new_event(tmp_path):
    path = recipe_journal.journal_path("run1", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"ts": 1, "event": "applied"}\n{"ts": 2, "ev')
    recipe_journal.append("run1", "finished", state_dir=tmp_path, now=4.0, ok=True)
    # The torn line has no newline; an append that continued it would lose
    # the new event too.
    assert [e["event"] for e in recipe_journal.read_events("run1", tmp_path)] == [
        "applied", "finished"]


def test_a_run_id_is_never_a_path(tmp_path):
    with pytest.raises(ValueError):
        recipe_journal.journal_path("../../etc/x", tmp_path)
    assert recipe_journal.append("../x", "applied", state_dir=tmp_path) is False


def test_the_log_reads_as_sentences():
    line = recipe_journal.describe_event(
        {"ts": 0, "event": "finished", "step": 1, "job": "c", "ok": True,
         "message": "done", "wrote": "/scope/run8_dataset"}, {"c": "Convert ND2 — run8"})
    assert line.endswith("step 2 (Convert ND2 — run8): succeeded — done (wrote /scope/run8_dataset)")


# ── the watch counts what arrived while it was down ───────────────────────────

def test_a_resumed_watch_counts_a_file_written_after_it_first_started():
    armed = 1000.0
    written_later = {"plate.nd2": (10, int((armed + 60) * 1e9))}
    resumed = SettleTracker(60, require_change=True, armed_at=armed)
    assert resumed.observe(written_later, 0).phase != WAITING
    assert resumed.observe(written_later, 60).phase == SETTLED
    fresh = SettleTracker(60, require_change=True)
    assert fresh.observe(written_later, 0).phase == WAITING


def test_a_file_from_before_the_watch_started_still_does_not_count():
    armed = 1000.0
    older = {"plate.nd2": (10, int((armed - 3600) * 1e9))}
    resumed = SettleTracker(60, require_change=True, armed_at=armed)
    assert resumed.observe(older, 0).phase == WAITING
    assert resumed.observe(older, 600).phase == WAITING


# ── a run through the real bridge ─────────────────────────────────────────────

class _Tool(QObject):
    jobFinished = Signal(bool, str, str)
    statusText = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict] = []

    def start_from_queue(self, spec: dict) -> None:
        self.started.append(spec)

    def show_job(self, spec: dict) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return False


def _fast(spec: dict) -> dict:
    return {"quiet_minutes": 0.3 / 60, "poll_seconds": 0.02, **spec}


def _recipe(tmp_path):
    q = JobQueue()
    q.add(JobSpec(id="w", kind="watch_folder", label="w",
                  spec=_fast({"folder": str(tmp_path / "run7")})))
    q.add(JobSpec(id="c", kind="nd2_import", label="c", spec={
        "source": str(tmp_path / "run7" / "run7.nd2"),
        "destination": str(tmp_path / "run7_dataset")}))
    q.set_link("c", "source", "w", kind=CHAIN_KIND)
    return recipe_from_chain("Nightly", q.jobs, "w")


@pytest.fixture
def setup(qtbot, tmp_path):
    state = tmp_path / "state"
    bridge = QueueBridge(state_dir=state)
    watch, nd2 = WatchFolderController(), _Tool()
    bridge.register("watch_folder", watch)
    bridge.register("nd2_import", nd2)
    actions = RecipeActions(bridge, state_dir=state, notify=lambda *a: None,
                            parent_widget=lambda: None)
    save_recipes([_recipe(tmp_path)], state)
    actions._recipes = [_recipe(tmp_path)]
    yield bridge, watch, nd2, actions, state
    watch.shutdown()
    qtbot.wait(150)


def test_a_recipe_run_journals_its_whole_story(setup, qtbot, tmp_path):
    bridge, _watch, nd2, actions, state = setup
    run8 = tmp_path / "run8"
    run8.mkdir()
    ids = actions.apply("Nightly", str(run8))
    jobs = [bridge.queue.get(i) for i in ids]
    run = jobs[0].recipe["run"]
    assert [j.recipe["step"] for j in jobs] == [0, 1]
    assert all(j.recipe == {**j.recipe, "run": run, "name": "Nightly",
                            "input": str(run8), "steps": 2} for j in jobs)
    bridge.start()
    qtbot.wait(100)
    (run8 / "run8.nd2").write_bytes(b"x" * 10)
    qtbot.waitUntil(lambda: bool(nd2.started), timeout=10000)
    nd2.statusText.emit(f"Converting {run8 / 'run8.nd2'} — the one ND2 file in the watched folder.")
    nd2.jobFinished.emit(True, "converted", str(tmp_path / "run8_dataset"))
    qtbot.wait(50)

    events = recipe_journal.read_events(run, state)
    story = [(e["event"], e.get("step")) for e in events]
    assert story[0] == ("applied", None)
    assert ("started", 0) in story and ("finished", 0) in story
    assert ("started", 1) in story and ("finished", 1) in story
    assert any(e["event"] == "status" and e["step"] == 0 and
               e["message"].startswith("Settled") for e in events)
    started_convert = next(e for e in events if e["event"] == "started" and e["step"] == 1)
    assert started_convert["inputs"] == {"source": str(run8)}
    assert any(e["event"] == "status" and e["step"] == 1 and "the one ND2 file" in e["message"]
               for e in events)
    finished_convert = next(e for e in events if e["event"] == "finished" and e["step"] == 1)
    assert finished_convert["ok"] is True
    assert finished_convert["wrote"] == str(tmp_path / "run8_dataset")
    # The countdown ticks many times; it is journalled once per phase.
    statuses = [e["message"].split(" — ")[0] for e in events if e["event"] == "status"]
    assert len(statuses) == len(set(statuses))
    log = actions.log_text(ids[1])
    assert "applied “Nightly”" in log and "step 2" in log


def test_after_a_crash_the_resumed_watch_counts_what_arrived_meanwhile(qtbot, tmp_path):
    """The app dies while a recipe's watch is running; the acquisition finishes
    while it is down. On the next launch the watch resumes with its first start
    time and settles on those files instead of waiting for a change for ever."""
    state = tmp_path / "state"
    run8 = tmp_path / "run8"
    run8.mkdir()
    first_start = time.time() - 600
    queue = JobQueue([JobSpec(
        id="w", kind="watch_folder", label="w", spec=_fast({"folder": str(run8)}),
        state=JobState.RUNNING, first_started_at=first_start,
        recipe={"run": "run8x", "name": "Nightly", "input": str(run8), "step": 0, "steps": 1})])
    save_queue(queue, state)
    (run8 / "run8.nd2").write_bytes(b"x" * 10)      # written while the app was down

    bridge = QueueBridge(state_dir=state)           # the next launch
    watch = WatchFolderController()
    bridge.register("watch_folder", watch)
    try:
        job = bridge.queue.get("w")
        assert job.state == JobState.INTERRUPTED
        assert job.first_started_at == first_start
        events = recipe_journal.read_events("run8x", state)
        assert [e["event"] for e in events] == ["interrupted"]
        # Saved at once, so the next launch does not journal it again.
        assert json.loads((state / "job_queue.json").read_text())["jobs"][0]["state"] == (
            JobState.INTERRUPTED)
        QueueBridge(state_dir=state)
        assert [e["event"] for e in recipe_journal.read_events("run8x", state)] == ["interrupted"]

        bridge.start()
        qtbot.waitUntil(lambda: bridge.queue.get("w").state == JobState.SUCCEEDED, timeout=5000)
    finally:
        watch.shutdown()
        qtbot.wait(150)


def test_retry_clears_the_first_start_so_a_new_watch_waits_for_new_files(tmp_path):
    q = JobQueue([JobSpec(id="w", kind="watch_folder", label="w", spec={},
                          state=JobState.FAILED, first_started_at=5.0)])
    assert q.retry("w")
    assert q.get("w").first_started_at is None


def test_a_job_s_recipe_and_first_start_survive_the_queue_file(tmp_path):
    job = JobSpec(id="w", kind="watch_folder", label="w", spec={}, first_started_at=12.5,
                  recipe={"run": "r1", "name": "N", "input": "/x", "step": 0, "steps": 2})
    loaded = JobSpec.from_json(json.loads(json.dumps(job.to_json())))
    assert loaded.first_started_at == 12.5 and loaded.recipe == job.recipe


def test_a_ticking_countdown_is_journalled_once_per_phase(tmp_path, qtbot):
    """The row's countdown changes every poll; the journal gets one line when
    the phase changes, not one per tick."""
    bridge = QueueBridge(state_dir=tmp_path)
    tool = _Tool()
    bridge.register("watch_folder", tool)
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w", spec={"folder": "/x"},
                           recipe={"run": "r1", "name": "N", "input": "/x", "step": 0,
                                   "steps": 1}))
    bridge.start()
    assert tool.started
    for text in ("Waiting for new files (0 files there now)…",
                 "Waiting for new files (2 files there now)…",
                 "Receiving files — 2 files, quiet for 0:05 of 5:00.",
                 "Receiving files — 3 files, quiet for 0:10 of 5:00.",
                 "Settled — 3 files, no change for 5:00."):
        tool.statusText.emit(text)
    assert bridge.queue.get("w").message == "Settled — 3 files, no change for 5:00."
    statuses = [e["message"] for e in recipe_journal.read_events("r1", tmp_path)
                if e["event"] == "status"]
    assert statuses == ["Waiting for new files (0 files there now)…",
                        "Receiving files — 2 files, quiet for 0:05 of 5:00.",
                        "Settled — 3 files, no change for 5:00."]


def test_a_duplicate_is_not_another_step_of_the_run(tmp_path, qtbot):
    bridge = QueueBridge(state_dir=tmp_path)
    bridge.add_job(JobSpec(id="w", kind="watch_folder", label="w", spec={"folder": "/x"},
                           recipe={"run": "r1", "name": "N", "input": "/x", "step": 0,
                                   "steps": 1}))
    bridge.duplicate_job("w")
    copy = next(j for j in bridge.queue.jobs if j.id != "w")
    assert copy.recipe is None


def test_the_row_names_the_recipe_and_offers_its_log(qtbot):
    from datasets.job_queue.row import JobRow

    job = JobSpec(id="c", kind="nd2_import", label="Convert ND2 — run8", spec={},
                  recipe={"run": "r1", "name": "Nightly", "input": "/x", "step": 1, "steps": 3})
    row = JobRow(job)
    qtbot.addWidget(row)
    row.update_from(job)
    assert "recipe “Nightly” · step 2/3" in row._detail_text
    assert "Recipe log…" in [a.text() for a in row.build_menu().actions()]
