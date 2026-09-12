"""Clicking a queue row takes you to that job's page.

Reported: clicking a **running** task did not take you to its progress. It
could not — the click landed in ``QueueBridge.show_job``, which returns early
when the kind's runner is busy, and ``JobQueueController`` dropped that ``False``
on the floor. The row's ``Edit…`` is disabled while running too, and ``Edit…``
was the only path that navigated, so a running job had no route to its page and
nothing said so.

The refusal itself was right, and is kept: ``show_job`` writes fields the
controller is actively driving as its own run-progress display, and
``set_running``'s field locks stop user clicks but not that programmatic path.
What was wrong is that it took navigation down with it. Populating is the unsafe
half; navigating is not, and for a running job the page already shows exactly
what the click is asking to see.

So the two shapes are separated here: a running job navigates and writes
nothing, anything else is populated and *then* shown — writing fields into a
page nobody is looking at being the same silence in a quieter form.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets.job_queue.controller import JobQueueController
from datasets.tab import DatasetsTab
from services.job_queue import JobSpec, JobState


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeController(QObject):
    jobFinished = Signal(bool, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.shown_specs: list[dict] = []
        self.busy = False

    def start_from_queue(self, spec: dict) -> None:
        pass

    def show_job(self, spec: dict) -> None:
        self.shown_specs.append(spec)

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return self.busy


def _controller(tmp_path, kind: str = "subset"):
    """A JobQueueController with one fake runner and recorded reveals/notices."""
    ctrl = JobQueueController(state_dir=tmp_path)
    runner = _FakeController()
    ctrl.register(kind, runner)
    revealed: list[str] = []
    notices: list[tuple[str, str]] = []
    ctrl.set_reveal_hook(lambda k: (revealed.append(k), True)[1])
    ctrl._notify = lambda text, kind="warn": notices.append((text, kind))
    return ctrl, runner, revealed, notices


def _add(ctrl, kind: str = "subset", state: JobState = JobState.PENDING) -> str:
    job = JobSpec(id="j1", kind=kind, label="a job", spec={"source": "/data"})
    job.state = state
    ctrl.bridge.queue.add(job)
    return job.id


def test_clicking_a_running_row_reveals_its_page(qapp, tmp_path):
    ctrl, runner, revealed, notices = _controller(tmp_path)
    job_id = _add(ctrl, state=JobState.RUNNING)
    runner.busy = True

    ctrl._on_show_requested(job_id)

    assert revealed == ["subset"], "A running job's page was not shown."
    assert notices == []


def test_clicking_a_running_row_does_not_write_to_its_page(qapp, tmp_path):
    """The whole reason the click used to be dropped. Navigation must not
    resurrect the overwrite it was protecting against."""
    ctrl, runner, revealed, _notices = _controller(tmp_path)
    job_id = _add(ctrl, state=JobState.RUNNING)
    runner.busy = True

    ctrl._on_show_requested(job_id)

    assert runner.shown_specs == [], (
        "show_job ran for a running job — that overwrites the fields the "
        "controller is using as its live progress display."
    )
    assert revealed == ["subset"]


def test_a_running_row_says_so_when_its_page_cannot_be_reached(qapp, tmp_path):
    """No shell to switch panes (Analyze outside a full window, or a
    Datasets-only profile). Silence here is the original defect."""
    ctrl, runner, _revealed, notices = _controller(tmp_path)
    ctrl.set_reveal_hook(lambda _kind: False)
    job_id = _add(ctrl, state=JobState.RUNNING)
    runner.busy = True

    ctrl._on_show_requested(job_id)

    assert notices, "A click that could not get anywhere said nothing."
    assert "watch the run" in notices[0][0]


def test_clicking_a_waiting_row_populates_then_reveals(qapp, tmp_path):
    ctrl, runner, revealed, notices = _controller(tmp_path)
    job_id = _add(ctrl, state=JobState.PENDING)

    ctrl._on_show_requested(job_id)

    assert runner.shown_specs == [{"source": "/data"}]
    assert revealed == ["subset"], (
        "A preview was written into a page the user was never taken to."
    )
    assert notices == []


def test_a_waiting_row_is_refused_while_its_tool_runs_another_job(qapp, tmp_path):
    """Busy runner, but this job is not the one running: the page cannot be
    repopulated, and showing it anyway would misdescribe whose fields those are."""
    ctrl, runner, revealed, notices = _controller(tmp_path)
    job_id = _add(ctrl, state=JobState.PENDING)
    runner.busy = True

    ctrl._on_show_requested(job_id)

    assert runner.shown_specs == []
    assert revealed == [], "Navigated to a page that does not describe this job."
    assert notices and "busy with another run" in notices[0][0]


def test_an_edit_session_still_refuses_the_click_outright(qapp, tmp_path):
    """Unchanged by this work: neither half may run while that page is under
    edit, or the next Save collects the previewed job's settings."""
    ctrl, runner, revealed, notices = _controller(tmp_path)
    first = _add(ctrl, state=JobState.PENDING)
    second = JobSpec(id="j2", kind="subset", label="other", spec={"source": "/b"})
    ctrl.bridge.queue.add(second)
    ctrl.sessions.begin_edit(first)
    runner.shown_specs.clear()
    revealed.clear()
    notices.clear()

    ctrl._on_show_requested(second.id)

    assert runner.shown_specs == []
    assert revealed == []
    assert notices and "Finish or cancel" in notices[0][0]


def test_the_reveal_hook_is_inert_until_datasets_tab_installs_one(qapp, tmp_path):
    """A controller built without a shell reports failure rather than
    pretending the page was shown."""
    ctrl = JobQueueController(state_dir=tmp_path)
    assert ctrl._reveal("subset") is False


# ── DatasetsTab's half ───────────────────────────────────────────────────────

def test_reveal_page_switches_the_rail_and_the_stack(qapp, tmp_path):
    tab = DatasetsTab(queue_state_dir=tmp_path)
    assert tab._reveal_page("subset") is True
    assert tab._nav.currentKey() == "Subset / Split"
    assert tab._pages.currentWidget() is tab._pages_by_kind["subset"]


def test_reveal_page_leaves_the_page_untouched(qapp, tmp_path):
    """It is the no-banner, no-write counterpart to _begin_session."""
    tab = DatasetsTab(queue_state_dir=tmp_path)
    tab._reveal_page("subset")
    assert tab._session_banner is None


def test_reveal_page_reports_failure_for_analyze_without_a_shell(qapp, tmp_path):
    """Analyze has no rail entry here, so reaching it needs the shell."""
    tab = DatasetsTab(queue_state_dir=tmp_path)
    assert tab._reveal_page("analyze") is False


def test_reveal_page_uses_the_shell_callback_for_analyze(qapp, tmp_path):
    calls: list[bool] = []
    tab = DatasetsTab(
        queue_state_dir=tmp_path,
        on_reveal_analyze=lambda: (calls.append(True), True)[1],
    )
    assert tab._reveal_page("analyze") is True
    assert calls == [True]


def test_datasets_tab_installs_the_reveal_hook_on_the_queue(qapp, tmp_path):
    """The wiring, not just the two halves: without this the controller keeps
    its inert default and every running row still goes nowhere.

    Asserted by driving the hook and looking at where the pane ended up, not by
    comparing it to the bound method — ``tab._reveal_page`` builds a new bound
    method on every attribute access, so an identity test fails against the very
    object it was handed.
    """
    tab = DatasetsTab(queue_state_dir=tmp_path)
    queue = tab._controllers["job_queue"]

    assert queue._reveal("subset") is True
    assert tab._pages.currentWidget() is tab._pages_by_kind["subset"]
