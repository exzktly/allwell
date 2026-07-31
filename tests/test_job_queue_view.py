"""Bare-`QApplication` widget tests for `datasets.job_queue.view`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from services.job_queue import JobSpec, JobState  # noqa: E402
from datasets.job_queue.view import _JobRow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _job(**kw):
    defaults = dict(id="j-1", kind="subset", label="Subset", spec={}, state=JobState.FAILED)
    defaults.update(kw)
    return JobSpec(**defaults)


def test_empty_state_text_lives_inside_the_list_not_below_it(qapp):
    """Regression guard (UX audit P2-1): the "No jobs queued yet" caption
    used to sit in a separate QLabel below a large, entirely blank
    QListWidget -- the empty container itself said nothing, reading as
    broken. It must instead be the list's own (sole, non-interactive) item,
    so the explanation lives inside the space it's explaining."""
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    assert page._list.count() == 1
    item = page._list.item(0)
    assert not (item.flags() & Qt.ItemIsSelectable)
    label = page._list.itemWidget(item)
    assert "No jobs queued yet" in label.text()

    page.set_jobs([JobSpec(id="j-1", kind="subset", label="Subset", spec={})])
    assert page._list.count() == 1
    assert isinstance(page._list.itemWidget(page._list.item(0)), _JobRow)

    page.set_jobs([])
    assert page._list.count() == 1
    assert "No jobs queued yet" in page._list.itemWidget(page._list.item(0)).text()


def test_row_shows_a_pointing_hand_cursor_and_a_hover_hint(qapp):
    """Regression guard (UX audit P1-11): the whole row is a click target
    (rowClicked -> "show this job"), but nothing about it looked clickable —
    the default arrow cursor and no tooltip gave no affordance at all."""
    row = _JobRow(_job())
    assert row.cursor().shape() == Qt.PointingHandCursor
    assert row.toolTip() != ""


def test_long_message_is_elided_not_hard_clipped(qapp):
    """Regression guard: a long job message used to be cut off mid-character
    by a bare setMaximumWidth(240) with no visual sign anything was missing —
    the full text was reachable only via the tooltip, itself undiscoverable.
    The label's own displayed text must now end with an ellipsis, and the
    tooltip must still carry the complete, un-elided message."""
    long_message = (
        "Predecessor j-9 (dataset root /very/long/path/that/would/never/fit) "
        "failed with: schema mismatch between dataset 1 and dataset 2"
    )
    job = _job(message=long_message)
    row = _JobRow(job)
    msg = next(lbl for lbl in row.findChildren(QLabel) if lbl.toolTip() == long_message)
    assert msg.text() != long_message
    assert msg.text().endswith("…")
    assert msg.toolTip() == long_message
