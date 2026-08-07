"""Tests for datasets/job_queue/controller.py's QueueBridge.

Uses a small fake QueueableController (a bare QObject with the three hook
methods and a jobFinished signal) rather than a real Datasets controller, so
these stay fast and focused on the bridge's own scheduling/attribution logic
— the real controllers' start_from_queue/show_job/jobFinished wiring is
covered by each feature's own tests plus tests/test_job_queue_e2e.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets.job_queue.controller import QueueBridge
from services.job_queue import JobSpec, JobState


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeController(QObject):
    jobFinished = Signal(bool, str, str)

    def __init__(self, *, cancellable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.started_specs: list[dict] = []
        self.shown_specs: list[dict] = []
        self.stop_calls = 0
        self._cancelled = False
        self._cancellable = cancellable
        self.busy = False

    def start_from_queue(self, spec: dict) -> None:
        self.started_specs.append(spec)

    def show_job(self, spec: dict) -> None:
        self.shown_specs.append(spec)

    def stop(self) -> None:
        self.stop_calls += 1
        if self._cancellable:
            self._cancelled = True

    def is_busy(self) -> bool:
        return self.busy

    def was_cancelled(self) -> bool:
        return self._cancelled

    def finish(self, *, ok: bool, message: str = "", path: str = "") -> None:
        """Test helper: simulate the controller's run completing."""
        self.jobFinished.emit(ok, message, path)


def _job(job_id, kind="widget", **kw):
    return JobSpec(id=job_id, kind=kind, label=job_id, spec={"n": job_id}, **kw)


def test_advance_does_nothing_until_started(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    assert runner.started_specs == []


def test_start_launches_first_pending_job(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.start()
    assert len(runner.started_specs) == 1
    assert bridge.queue.get("a").state == JobState.RUNNING


def test_job_finished_marks_succeeded_and_advances_to_next(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.queue.add(_job("b"))
    bridge.start()
    assert len(runner.started_specs) == 1

    runner.finish(ok=True, message="done", path="/out/a")
    assert bridge.queue.get("a").state == JobState.SUCCEEDED
    assert len(runner.started_specs) == 2  # b started automatically
    assert bridge.queue.get("b").state == JobState.RUNNING


def test_chained_job_receives_predecessors_output(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.queue.add(_job("b", chain_from="a", chain_target="n", chain_kind="dataset_root"))
    bridge.start()
    runner.finish(ok=True, path="/out/a")

    assert runner.started_specs[-1]["n"] == "/out/a"


def test_failed_job_cascades_block_and_does_not_start_successor(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.queue.add(_job("b", chain_from="a"))
    bridge.start()
    runner.finish(ok=False, message="boom")

    assert bridge.queue.get("a").state == JobState.FAILED
    assert bridge.queue.get("b").state == JobState.BLOCKED
    assert len(runner.started_specs) == 1  # b never started


def test_mismatched_kind_completion_is_dropped(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner_a = _FakeController()
    runner_b = _FakeController()
    bridge.register("widget-a", runner_a)
    bridge.register("widget-b", runner_b)
    bridge.queue.add(_job("a", kind="widget-a"))
    bridge.start()

    # A manual Run on the OTHER tool's own page finishes while "a" is still
    # the queue's in-flight job — must not be misattributed to "a".
    runner_b.finish(ok=True, path="/out/manual")

    assert bridge.queue.get("a").state == JobState.RUNNING
    assert bridge.current_job_id() == "a"


def test_stop_current_pauses_queue_and_calls_runner_stop(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController(cancellable=True)
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.queue.add(_job("b"))
    bridge.start()

    bridge.stop_current()
    assert runner.stop_calls == 1
    assert bridge.queue.is_started is False

    # The stopped job's async completion still lands correctly as cancelled.
    runner.finish(ok=False, message="Pipeline exited with code -15.")
    assert bridge.queue.get("a").state == JobState.CANCELLED
    # Queue stayed paused - "b" must not have started.
    assert len(runner.started_specs) == 1


def test_show_job_delegates_to_matching_runner(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))

    bridge.show_job("a")
    assert runner.shown_specs == [{"n": "a"}]


def test_show_job_does_nothing_while_the_runner_is_busy(qapp, tmp_path):
    """Regression guard: QueueBridge.show_job used to call runner.show_job()
    unconditionally, with no check that the runner was already mid-run (e.g.
    from a manual Run press on that tool's own page). For the Datasets
    controllers show_job() writes fields directly -- bypassing the run-time
    field locks, which only block user clicks, not this programmatic path --
    including fields a controller is actively repainting as its own run
    progress display (Subset's plate map)."""
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    runner.busy = True
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))

    bridge.show_job("a")
    assert runner.shown_specs == []


def test_show_job_passes_a_copy_not_the_persisted_spec(qapp, tmp_path):
    """Regression guard: QueueBridge.show_job used to hand a runner the
    queue's own live persisted dict, not a copy -- a show_job()
    implementation that mutated its argument (nothing does today, but
    nothing prevented it either) would silently corrupt the saved queue."""
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    job = _job("a")
    bridge.queue.add(job)

    bridge.show_job("a")
    received = runner.shown_specs[0]
    assert received == job.spec
    assert received is not job.spec
    received["n"] = "mutated"
    assert bridge.queue.get("a").spec == {"n": "a"}


def test_remove_job_refuses_the_running_job(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.start()

    bridge.remove_job("a")
    assert bridge.queue.get("a") is not None  # still there - refused


def test_persists_across_bridge_instances(qapp, tmp_path):
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeController()
    bridge.register("widget", runner)
    bridge.queue.add(_job("a"))
    bridge.start()
    runner.finish(ok=True, path="/out/a")

    reopened = QueueBridge(state_dir=tmp_path)
    assert reopened.queue.get("a").state == JobState.SUCCEEDED
    assert reopened.queue.is_started is False  # never auto-starts


# ── drag reorder vs menu reorder ─────────────────────────────────────────────

def _controller(tmp_path, ids):
    from datasets.job_queue.controller import JobQueueController
    from services.job_queue import JobSpec

    c = JobQueueController(state_dir=tmp_path)
    for job_id in ids:
        c.bridge.add_job(JobSpec(id=job_id, kind="subset", label=job_id, spec={}))
    return c


def test_a_dragged_row_shifts_the_rows_it_passes(qapp, tmp_path):
    """Regression: the drag signal was wired to move_job, which *swaps*.

    Dragging a row three down then exchanged it with whatever sat there,
    teleporting that job to where this one started, instead of shuffling the
    rows it passed up by one. move_job_to was unreachable as a result.
    """
    c = _controller(tmp_path, "abcd")
    c.page.moveByRequested.emit("a", 2)
    assert [j.id for j in c.bridge.queue.jobs] == ["b", "c", "a", "d"]


def test_a_drag_past_the_end_clamps_rather_than_doing_nothing(qapp, tmp_path):
    """Dropping a row below the last one plainly means "put it last"."""
    c = _controller(tmp_path, "abcd")
    c.page.moveByRequested.emit("a", 99)
    assert [j.id for j in c.bridge.queue.jobs] == ["b", "c", "d", "a"]


def test_the_menu_move_is_still_a_single_step(qapp, tmp_path):
    """Move up / Move down stay a neighbour swap — the drag change must not
    have quietly redefined them."""
    c = _controller(tmp_path, "abcd")
    c.page.moveDownRequested.emit("a")
    assert [j.id for j in c.bridge.queue.jobs] == ["b", "a", "c", "d"]


def test_a_drag_that_would_break_a_chain_is_refused(qapp, tmp_path):
    c = _controller(tmp_path, "abc")
    c.bridge.set_chain("c", "a")
    c.page.moveByRequested.emit("c", -2)
    assert [j.id for j in c.bridge.queue.jobs] == ["a", "b", "c"]


def test_a_chained_successor_of_analyze_gets_the_dataset_root(qapp, tmp_path):
    """Regression: it used to get Analyze's `out/` folder.

    AnalyzeTab reports its output directory, but every successor takes a
    dataset root — the folder holding the `in/` + `out/` pair. Handed `out/`,
    Subset looks for `out/in/`, finds no wells, and quietly subsets nothing.
    """
    from datasets.job_queue.controller import JobQueueController
    from services.job_queue import JobSpec

    c = JobQueueController(state_dir=tmp_path)
    c.bridge.add_job(JobSpec(id="a", kind="analyze", label="A",
                             spec={"opts": {"raw": "/data/plateA"}}))
    c.bridge.add_job(JobSpec(id="v", kind="validate", label="V",
                             spec={"dataset": "/PLACEHOLDER"}))
    c.bridge.set_chain("v", "a")
    c.bridge._current = ("a", "analyze")
    c.bridge.queue.mark_running("a")
    c.bridge._on_job_finished("analyze", True, "", "/data/plateA_processed/out")

    assert c.bridge.queue.materialise("v") == {"dataset": "/data/plateA_processed"}
