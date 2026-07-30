"""Smoke tests for DatasetsTab's Job Queue wiring: the page builds with all
six tool controllers plus a fake Analyze registered, and "+ Queue" on a
tool's page lands a job the Job Queue page can see.

Offscreen, no real pipeline or worker execution — this only checks the
registration/wiring datasets_tab.py and datasets/job_queue/controller.py do,
not any tool's actual run behaviour (covered by each feature's own tests).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets_tab import DatasetsTab


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeAnalyzeController(QObject):
    """Duck-types datasets.job_queue.controller.QueueableController plus the
    collect_spec() hook AnalyzeRunMixin adds — standing in for AnalyzeTab so
    this test doesn't need a real one built."""

    jobFinished = Signal(bool, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.started_specs: list[dict] = []

    def collect_spec(self) -> dict:
        return {"opts": {"raw": "/data/plate7"}}

    def start_from_queue(self, spec: dict) -> None:
        self.started_specs.append(spec)

    def show_job(self, spec: dict) -> None:
        pass

    def stop(self) -> None:
        pass


def _make_tab(tmp_path, *, analyze=True):
    fake_analyze = _FakeAnalyzeController() if analyze else None
    tab = DatasetsTab(
        analyze_controller=fake_analyze,
        queue_state_dir=tmp_path,
    )
    return tab, fake_analyze


def test_builds_seven_pages_including_job_queue(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    try:
        names = [tab._pages.widget(i) is not None for i in range(tab._pages.count())]
        assert len(names) == 7
    finally:
        tab.shutdown()


def test_job_queue_controller_has_all_kinds_registered(qapp, tmp_path):
    tab, fake_analyze = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        for kind in ("concatenate", "validate", "subset", "import_loose", "merge_plates", "analyze"):
            assert kind in jq.bridge._runners
    finally:
        tab.shutdown()


def test_add_to_queue_from_a_tool_lands_a_job_in_the_bridge(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("concatenate", {"dataset1": "/a", "dataset2": "/b", "destination": "/c"})
        jq = tab._controllers["job_queue"]
        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "concatenate"
        assert jobs[0].spec["destination"] == "/c"
    finally:
        tab.shutdown()


def test_queue_current_analyze_run_button_adds_analyze_job(qapp, tmp_path):
    tab, fake_analyze = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        jq.page.addAnalyzeRequested.emit()
        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "analyze"
        assert jobs[0].spec["opts"]["raw"] == "/data/plate7"
    finally:
        tab.shutdown()


def test_starting_queue_runs_the_queued_analyze_job(qapp, tmp_path):
    tab, fake_analyze = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        jq.add_job("analyze", {"opts": {"raw": "/data/plate7"}})
        jq.page.startToggled.emit()
        assert len(fake_analyze.started_specs) == 1
    finally:
        tab.shutdown()


def test_without_analyze_controller_analyze_button_is_a_no_op(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path, analyze=False)
    try:
        jq = tab._controllers["job_queue"]
        jq.page.addAnalyzeRequested.emit()  # must not raise
        assert jq.bridge.queue.jobs == []
    finally:
        tab.shutdown()


def test_shutdown_does_not_raise(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    tab.shutdown()  # must not raise even with nothing running
