"""The Job Queue's **Clear all** button.

Clear completed keeps what is still waiting; Clear all empties the queue, so it
drops jobs that never ran as well and is asked about first. Everything here
presses the real button and reads what the queue and the question say, not the
controller method behind it.

A running job is the one thing it leaves: its worker is writing to disk and will
report back to its row, and ``QueueBridge.remove_job`` refuses it for the same
reason.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from datasets.job_queue.controller import JobQueueController  # noqa: E402
from services.job_queue import JobSpec, JobState, load_queue  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _job(job_id, kind="validate"):
    return JobSpec(id=job_id, kind=kind, label=job_id, spec={"dataset": f"/{job_id}"})


def _controller(tmp_path):
    return JobQueueController(state_dir=tmp_path)


def _populate(ctl, **states):
    """Add one job per keyword (state as the value) and put each in its state."""
    queue = ctl.bridge.queue
    for job_id, state in states.items():
        queue.add(_job(job_id))
    for job_id, state in states.items():
        if state == JobState.PENDING:
            continue
        queue.get(job_id).state = state
    ctl.bridge.queueChanged.emit()


@pytest.fixture
def asked(monkeypatch):
    """Record each confirmation and answer it as *answer[0]* says."""
    seen: list[dict] = []
    answer = [QMessageBox.Yes]

    def question(parent, title, text, buttons=None, default=None):
        seen.append({"title": title, "text": text, "default": default})
        return answer[0]

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    seen_answer = type("A", (), {"set": staticmethod(lambda v: answer.__setitem__(0, v)),
                                 "calls": seen})
    return seen_answer


def _ids(ctl):
    return [j.id for j in ctl.bridge.queue.jobs]


def test_yes_removes_every_job_that_is_not_running_and_keeps_the_running_one(
        qapp, tmp_path, asked):
    ctl = _controller(tmp_path)
    _populate(ctl, a=JobState.SUCCEEDED, b=JobState.FAILED, c=JobState.RUNNING,
              d=JobState.PENDING, e=JobState.SUSPENDED, f=JobState.BLOCKED)
    ctl.page._clear_all_btn.click()
    assert len(asked.calls) == 1
    assert _ids(ctl) == ["c"]
    assert ctl.bridge.queue.get("c").state == JobState.RUNNING


def test_the_answer_is_persisted(qapp, tmp_path, asked):
    ctl = _controller(tmp_path)
    _populate(ctl, a=JobState.PENDING, b=JobState.SUCCEEDED)
    ctl.page._clear_all_btn.click()
    assert [j.id for j in load_queue(tmp_path).jobs] == []


def test_no_removes_nothing(qapp, tmp_path, asked):
    asked.set(QMessageBox.No)
    ctl = _controller(tmp_path)
    _populate(ctl, a=JobState.PENDING, b=JobState.SUCCEEDED)
    ctl.page._clear_all_btn.click()
    assert len(asked.calls) == 1
    assert _ids(ctl) == ["a", "b"]


def test_the_question_defaults_to_no_and_states_what_goes(qapp, tmp_path, asked):
    ctl = _controller(tmp_path)
    _populate(ctl, a=JobState.PENDING, b=JobState.PENDING, c=JobState.SUCCEEDED,
              d=JobState.FAILED, e=JobState.RUNNING)
    ctl.page._clear_all_btn.click()
    call = asked.calls[0]
    assert call["default"] == QMessageBox.No
    assert "Remove 4 job(s)" in call["text"]
    assert "2 waiting to run" in call["text"]
    assert "1 done" in call["text"] and "1 failed" in call["text"]
    # The running job is neither counted in the 4 nor listed as going.
    assert "running" in call["text"].lower() and "not removed" in call["text"]


def test_the_question_does_not_mention_a_running_job_when_there_is_none(
        qapp, tmp_path, asked):
    ctl = _controller(tmp_path)
    _populate(ctl, a=JobState.PENDING)
    ctl.page._clear_all_btn.click()
    assert "not removed" not in asked.calls[0]["text"]


def test_the_button_is_enabled_only_when_something_would_be_removed(
        qapp, tmp_path, asked):
    ctl = _controller(tmp_path)
    btn = ctl.page._clear_all_btn
    assert not btn.isEnabled()                      # an empty queue
    _populate(ctl, a=JobState.RUNNING)
    assert not btn.isEnabled()                      # only a running job
    _populate(ctl, b=JobState.PENDING)
    assert btn.isEnabled()
    btn.click()
    assert not btn.isEnabled()                      # back to just the running job


def test_a_stale_press_on_an_empty_queue_says_so_and_asks_nothing(
        qapp, tmp_path, asked):
    ctl = _controller(tmp_path)
    notices: list[tuple[str, str]] = []
    ctl._notify = lambda text, kind="warn": notices.append((kind, text))
    ctl.page.clearAllRequested.emit()
    assert asked.calls == []
    assert notices == [("neutral", "Nothing to clear.")]


def test_a_job_chained_to_a_removed_job_does_not_survive_pointing_at_it(
        qapp, tmp_path, asked):
    """Only the running job survives, so no survivor can hold a link to a removed
    one; the assertion is on the link itself, which is what a half-removal
    would leave behind."""
    ctl = _controller(tmp_path)
    _populate(ctl, a=JobState.SUCCEEDED, b=JobState.RUNNING)
    ctl.bridge.queue.get("a").dataset_root = "/A"
    ctl.bridge.set_chain("b", "a")
    ctl.page._clear_all_btn.click()
    survivor = ctl.bridge.queue.get("b")
    assert survivor is not None and survivor.sources() == []
