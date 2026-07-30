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

    def start_from_queue(self, spec: dict) -> None:
        self.started_specs.append(spec)

    def show_job(self, spec: dict) -> None:
        self.shown_specs.append(spec)

    def stop(self) -> None:
        self.stop_calls += 1
        if self._cancellable:
            self._cancelled = True

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
