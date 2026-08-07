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
        self.queue_sink = None
        self.hydrated: list[dict] = []

    def collect_spec(self) -> dict:
        return {"opts": {"raw": "/data/plate7"}}

    def start_from_queue(self, spec: dict) -> None:
        self.started_specs.append(spec)

    def show_job(self, spec: dict) -> None:
        pass

    def hydrate_job(self, spec: dict) -> None:
        """The real AnalyzeTab has one; the queue prefers it over show_job."""
        self.hydrated.append(spec)

    def set_queue_sink(self, sink) -> None:
        self.queue_sink = sink

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return False


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


def test_analyze_s_own_queue_button_lands_a_job(qapp, tmp_path):
    """Analyze's page gained its own "+ Queue", wired through a sink the
    Datasets side injects — so neither module imports the other."""
    tab, fake_analyze = _make_tab(tmp_path)
    try:
        assert fake_analyze.queue_sink is not None
        fake_analyze.queue_sink(fake_analyze.collect_spec())
        jobs = tab._controllers["job_queue"].bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "analyze"
        assert jobs[0].spec["opts"]["raw"] == "/data/plate7"
    finally:
        tab.shutdown()


def test_a_queued_job_is_named_after_what_it_runs(qapp, tmp_path):
    """Every row used to read the bare tool name, so eight queued subsets were
    indistinguishable and picking the right one to remove was guesswork."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/plateA", "destination": "/d/plateA_sub"})
        job = tab._controllers["job_queue"].bridge.queue.jobs[0]
        assert job.label == "Subset — plateA → plateA_sub"
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


def test_without_an_analyze_controller_the_analyze_entry_is_a_no_op(qapp, tmp_path):
    """A profile build can drop Analyze entirely; the menu entry must refuse
    rather than raise."""
    tab, _ = _make_tab(tmp_path, analyze=False)
    try:
        jq = tab._controllers["job_queue"]
        assert jq.begin_new_job("analyze") is False
        assert jq.bridge.queue.jobs == []
    finally:
        tab.shutdown()


# ── edit / new-job sessions ──────────────────────────────────────────────────

def test_editing_a_job_opens_its_page_with_a_banner(qapp, tmp_path):
    """The whole point of the banner: without it the tool's page looks exactly
    as it always does, and there is no way to tell you are editing a queued
    job rather than setting up a fresh run."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id

        assert jq.begin_edit(job_id) is True
        assert jq.session() == ("subset", job_id)
        assert tab._session_banner is not None
        assert "Editing queued job" in tab._session_banner.text()
        # And the rail actually went there — a banner on a page the user
        # cannot see is no better than no banner.
        assert tab._pages.currentName() == "Subset / Split"
    finally:
        tab.shutdown()


def test_saving_an_edit_writes_back_to_the_same_job(qapp, tmp_path):
    """Not a second job appended at the tail, which is what the old
    remove-and-re-add workflow produced."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id
        jq.begin_edit(job_id)

        subset_page = tab._controllers["subset"].page
        subset_page.set_destination_path("/d/edited")
        tab._save_session()

        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].id == job_id
        assert jobs[0].spec["destination"] == "/d/edited"
        # The name follows the spec it describes.
        assert jobs[0].label.endswith("→ edited")
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_cancelling_an_edit_leaves_the_job_alone(qapp, tmp_path):
    """Cancel goes through the controller, not just the banner: tearing the
    strip down on its own would leave a session the controller still believed
    in and would happily save later."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id
        jq.begin_edit(job_id)
        tab._controllers["subset"].page.set_destination_path("/d/edited")

        tab._cancel_session()
        assert jq.session() is None
        assert jq.bridge.queue.jobs[0].spec["destination"] == "/d/b"
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_a_new_job_session_adds_rather_than_updates(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        assert jq.begin_new_job("validate") is True
        assert jq.session() == ("validate", None)
        assert "new Validate job" in tab._session_banner.text()

        tab._controllers["validate"].page.set_dataset_path("/d/plate7")
        tab._save_session()

        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "validate"
        assert jobs[0].spec["dataset"] == "/d/plate7"
    finally:
        tab.shutdown()


def test_a_session_is_abandoned_when_its_job_starts_running(qapp, tmp_path):
    """Saving it then would write over a spec already handed to a runner."""
    tab, _ = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        jq.add_job("analyze", {"opts": {"raw": "/data/plate7"}})
        job_id = jq.bridge.queue.jobs[0].id
        jq.begin_edit(job_id)
        assert jq.session() is not None

        jq.page.startToggled.emit()          # the job is now running
        assert jq.session() is None
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_editing_is_refused_while_that_tool_has_its_own_run_going(qapp, tmp_path):
    """show_job writes fields the controller drives as its progress display,
    and set_running's locks only block user clicks, not that path."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id

        subset = tab._controllers["subset"]
        subset.is_busy = lambda: True
        assert jq.begin_edit(job_id) is False
        assert jq.session() is None
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_shutdown_does_not_raise(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    tab.shutdown()  # must not raise even with nothing running
