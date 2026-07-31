"""Bare-`QApplication` widget tests for `datasets.job_queue.view`."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QScrollArea, QToolButton  # noqa: E402

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


def test_row_has_a_bottom_divider(qapp):
    """Regression guard (UX audit P2-11): eight nearly-identical rows with
    no separation at all read as one undifferentiated block."""
    row = _JobRow(_job())
    assert "border-bottom" in row.styleSheet()


def test_only_pending_rows_get_an_ordinal(qapp):
    """Regression guard (UX audit P2-11): "strictly serial … in order" was
    the blurb's own promise, but nothing on the page said what order --
    succeeded/failed/cancelled rows interleaved with pending ones with no
    position numbers. Only pending rows (queue positions still ahead) get
    one; a finished row's number wouldn't mean "runs next" anymore."""
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    jobs = [
        _job(id="j-1", state=JobState.SUCCEEDED),
        _job(id="j-2", state=JobState.PENDING),
        _job(id="j-3", state=JobState.PENDING),
        _job(id="j-4", state=JobState.RUNNING),
    ]
    page.set_jobs(jobs)
    rows = [page._list.itemWidget(page._list.item(i)) for i in range(page._list.count())]
    ordinal_texts = [
        next(lbl for lbl in row.findChildren(QLabel) if lbl.objectName() == "Muted"
             and lbl.parent() is row).text()
        for row in rows
    ]
    assert ordinal_texts == ["", "1.", "2.", ""]


def test_up_down_hidden_on_terminal_rows_shown_on_pending(qapp):
    """Regression guard (UX audit P2-11): up/down/reorder controls appeared
    on every row including finished ones, where reordering means nothing."""
    def _tool_button_count(row):
        return len([w for w in row.findChildren(QToolButton)
                    if w.toolTip() in ("Move up", "Move down")])

    for state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED,
                  JobState.BLOCKED, JobState.INTERRUPTED):
        row = _JobRow(_job(state=state))
        assert _tool_button_count(row) == 0, state

    for state in (JobState.PENDING, JobState.RUNNING):
        row = _JobRow(_job(state=state))
        assert _tool_button_count(row) == 2, state


def test_running_row_shows_an_indeterminate_progress_bar(qapp):
    """Regression guard (UX audit P2-11): the running row looked identical
    to an idle pending row until it finished -- no per-job percentage is
    ever reported, so an indeterminate bar is the only signal available
    that says "this one specifically is active right now"."""
    running = _JobRow(_job(state=JobState.RUNNING))
    assert running.findChildren(QProgressBar)

    pending = _JobRow(_job(state=JobState.PENDING))
    assert not pending.findChildren(QProgressBar)


def test_page_is_wrapped_in_a_scroll_area(qapp):
    """Regression guard (UX audit §8.8): Job Queue was the one Datasets page
    of seven with no QScrollArea -- a long enough job list could crush into
    whatever space was left the same way the other six were already fixed
    against."""
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    outer_scroll = page.layout().itemAt(0).widget()
    assert isinstance(outer_scroll, QScrollArea)
    assert outer_scroll.widgetResizable() is True


def test_start_queue_is_the_first_left_aligned_header_button(qapp):
    """Regression guard (UX audit P2-11): every other Datasets page puts its
    primary action at the row's left edge; Job Queue pushed "Start queue"
    far right with a secondary button on the left -- the one page in the
    pane with its hierarchy backwards."""
    from datasets.job_queue.view import JobQueuePage

    page = JobQueuePage()
    header = page._start_btn.parentWidget()
    widgets = [header.layout().itemAt(i).widget() for i in range(header.layout().count())]
    widgets = [w for w in widgets if w is not None]
    assert widgets[0] is page._start_btn
    assert page._start_btn.property("variant") == "primary"
    assert widgets.index(page._add_analyze_btn) > widgets.index(page._start_btn)
