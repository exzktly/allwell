"""A failed queued job says why, on its row and in full on request.

The reason was stored (``JobQueue.mark_finished`` keeps the message) but shown
only at the *end* of the row's elided second line, behind the job's paths, so
at any real window width a failed row said nothing but "failed". And the
messages that matter most — Analyze's input-layout refusal — run to several
lines, which an elided label cuts to nothing.

Behaviour rather than appearance, so this file stays out of the UI lane and
runs in CI.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from services.job_queue import JobQueue, JobSpec, JobState
from datasets.job_queue.row import JobRow, failure_reason

REASON = ("No per-well zips (A01.zip …) in in/ — nothing to process.\n\n"
          "Expected one of:\n  • Selected folder is named \"in\" …\n\n"
          "Folder selected: /data/plate/in")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _failed_job(message: str, *, cancelled: bool = False) -> JobSpec:
    """A job failed the way the queue fails one: through ``mark_finished``."""
    queue = JobQueue()
    job = JobSpec(id="j-1", kind="analyze", label="Analyze — plate",
                  spec={"opts": {"raw": "/data/plate/in/with/a/very/long/path"}})
    queue.add(job)
    queue.mark_running("j-1")
    queue.mark_finished("j-1", ok=False, message=message, cancelled=cancelled)
    return queue.get("j-1")


def _action(menu, text):
    return next((a for a in menu.actions() if a.text() == text), None)


def test_the_reason_leads_the_row_and_the_tooltip_holds_all_of_it(qapp):
    row = JobRow(_failed_job(REASON))
    assert row._detail_text.startswith(
        "Failed — No per-well zips (A01.zip …) in in/ — nothing to process.  ·  ")
    assert "\n" not in row._detail_text
    assert row._detail.toolTip().startswith(f"Failed — {REASON}")


def test_why_it_failed_shows_the_whole_message(qapp):
    row = JobRow(_failed_job(REASON))
    menu = row.build_menu()
    action = _action(menu, "Why it failed…")
    assert action is not None
    action.trigger()
    box = row._failure_box
    assert box is not None and box.isVisible()
    assert box.text() == f"Failed — {REASON}"
    assert not box.isModal()
    box.close()


def test_a_failure_with_no_message_still_says_so(qapp):
    row = JobRow(_failed_job(""))
    assert row._detail_text.startswith("Failed — no reason was reported.")
    assert _action(row.build_menu(), "Why it failed…") is not None


def test_a_cancel_message_is_not_prefixed_twice(qapp):
    job = _failed_job("Cancelled by user.", cancelled=True)
    assert job.state == JobState.CANCELLED
    assert failure_reason(job) == "Cancelled by user."


@pytest.mark.parametrize("state", [JobState.PENDING, JobState.RUNNING,
                                   JobState.SUCCEEDED, JobState.BLOCKED])
def test_only_a_failed_or_cancelled_job_offers_it(qapp, state):
    job = JobSpec(id="j-1", kind="subset", label="Subset", spec={},
                  state=state, message="something")
    assert failure_reason(job) is None
    assert _action(JobRow(job).build_menu(), "Why it failed…") is None
