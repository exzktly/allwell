"""Bare-`QApplication` widget tests for `datasets.job_queue.view` and its rows.

The rows live in a plain box layout rather than a `QListWidget`, so the
structural assertions here go through `page._rows` (id -> JobRow) instead of
item indices — the intent of each guard is unchanged.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from services.job_queue import JobSpec, JobState  # noqa: E402
from datasets.job_queue.row import KIND_ICON, JobRow  # noqa: E402
from datasets.job_queue.view import ADD_KINDS, JobQueuePage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _job(**kw):
    defaults = dict(id="j-1", kind="subset", label="Subset", spec={},
                    state=JobState.FAILED)
    defaults.update(kw)
    return JobSpec(**defaults)


def _rows(page):
    return [page._rows[job_id] for job_id in page._order]


def _actions(row):
    return [a.text() for a in row.build_menu().actions() if a.text()]


def _chain_submenu(row):
    """The "Run after" submenu, with its parent menu kept alive.

    Dropping the parent takes the C++ QMenu with it and every action under it
    becomes a dangling reference, so the caller has to hold both.
    """
    menu = row.build_menu()
    sub = next(a.menu() for a in menu.actions() if a.text() == "Run after")
    return menu, sub


# ── page shell ────────────────────────────────────────────────────────────────

def test_an_empty_queue_is_a_caption_not_a_page_height_box(qapp):
    """The "No jobs queued yet" sentence is worth keeping — it is the only
    thing on this page saying where jobs come from. The box around it was not:
    putting the sentence inside the list meant showing the list, and the list
    is a bordered panel carrying a layout stretch, so one line of text
    rendered as a page-height empty container. The caption stands on its own
    and the list appears only when it has rows."""
    page = JobQueuePage()
    assert page._list.isHidden()
    assert not page._rows
    assert not page._empty_hint.isHidden()

    page.set_jobs([_job()])
    assert not page._list.isHidden()
    assert page._empty_hint.isHidden()
    assert len(page._rows) == 1
    assert isinstance(_rows(page)[0], JobRow)

    page.set_jobs([])
    assert page._list.isHidden()
    assert not page._rows
    assert not page._empty_hint.isHidden()


def test_page_is_wrapped_in_a_scroll_area(qapp):
    """Regression guard (UX audit §8.8): Job Queue was the one Datasets page
    of seven with no QScrollArea -- a long enough job list could crush into
    whatever space was left the same way the other six were already fixed
    against."""
    page = JobQueuePage()
    outer_scroll = page.layout().itemAt(0).widget()
    assert isinstance(outer_scroll, QScrollArea)
    assert outer_scroll.widgetResizable() is True


def test_start_queue_is_the_first_left_aligned_header_button(qapp):
    """Regression guard (UX audit P2-11): every other Datasets page puts its
    primary action at the row's left edge; Job Queue pushed "Start queue"
    far right with a secondary button on the left -- the one page in the
    pane with its hierarchy backwards."""
    page = JobQueuePage()
    header = page._start_btn.parentWidget()
    widgets = [header.layout().itemAt(i).widget() for i in range(header.layout().count())]
    widgets = [w for w in widgets if w is not None]
    assert widgets[0] is page._start_btn
    assert page._start_btn.property("variant") == "primary"
    assert widgets.index(page._add_btn) > widgets.index(page._start_btn)


def test_add_job_menu_offers_every_kind(qapp):
    """The page can answer "how do I add one?" by itself.

    Before this the header had a button for Analyze alone, and every other
    kind had to be started from its own page — which the empty caption named
    but the page could not reach.
    """
    page = JobQueuePage()
    seen = []
    page.addKindRequested.connect(seen.append)
    menu = page.build_add_menu()
    assert len(menu.actions()) == len(ADD_KINDS)
    menu.actions()[0].trigger()
    assert seen == [ADD_KINDS[0]]


def test_summary_omits_states_with_no_jobs(qapp):
    """"0 failed" is not news, and a row of zeroes is noise."""
    page = JobQueuePage()
    page.set_summary({JobState.PENDING: 2, JobState.FAILED: 1,
                      JobState.SUCCEEDED: 0})
    assert page._summary.text() == "2 pending  ·  1 failed"


def test_repair_notes_are_shown_when_the_load_had_to_fix_something(qapp):
    """A repair the user cannot see reads as the queue quietly losing state."""
    page = JobQueuePage()
    assert page._notes.isHidden()
    page.set_repair_notes(["j-1: was running at last save, marked interrupted"])
    assert not page._notes.isHidden()
    assert "interrupted" in page._notes.text()


# ── row ───────────────────────────────────────────────────────────────────────

def test_row_shows_a_pointing_hand_cursor_and_a_hover_hint(qapp):
    """Regression guard (UX audit P1-11): the whole row is a click target
    (rowClicked -> "show this job"), but nothing about it looked clickable —
    the default arrow cursor and no tooltip gave no affordance at all."""
    row = JobRow(_job())
    assert row.cursor().shape() == Qt.PointingHandCursor
    assert row.toolTip() != ""


def test_the_row_divider_does_not_cascade_onto_its_children(qapp):
    """Regression guard: the divider used to be a bare `border-bottom` set on
    the row widget with no selector, so Qt applied it to every child as well
    and each label, tool button and progress bar drew its own underline. The
    rule now lives in theme.qss keyed on the row's object name, and nothing
    inside the row carries a stylesheet of its own."""
    row = JobRow(_job())
    assert row.objectName() == "JobRow"
    assert row.styleSheet() == ""
    offenders = [w for w in row.findChildren(object)
                 if hasattr(w, "styleSheet") and "border-bottom" in (w.styleSheet() or "")]
    assert not offenders

    from ui import theme
    assert "QWidget#JobRow" in theme.qss()


def test_long_detail_is_elided_not_hard_clipped(qapp):
    """Regression guard: a long job message used to be cut off mid-character
    by a bare setMaximumWidth(240) with no visual sign anything was missing —
    the full text was reachable only via the tooltip, itself undiscoverable.
    The elision is now against the width the label is actually given, so it
    tracks the pane instead of a magic number."""
    long_message = (
        "Predecessor j-9 (dataset root /very/long/path/that/would/never/fit) "
        "failed with: schema mismatch between dataset 1 and dataset 2"
    )
    row = JobRow(_job(message=long_message))
    row.resize(300, 48)
    row._detail.resize(200, 16)
    row._apply_elision()
    assert row._detail.text() != long_message
    assert row._detail.text().endswith("…")
    assert row._detail.toolTip() == long_message


def test_detail_line_is_hidden_when_there_is_nothing_to_say(qapp):
    """An empty second line would leave every row taller for nothing."""
    row = JobRow(_job(spec={}, message=""))
    assert row._detail.isHidden()


def test_detail_line_names_the_predecessor_by_label_not_by_id(qapp):
    """A ten-hex-character job id tells the user nothing about which job
    this one is waiting for."""
    job = _job(id="j-2", chain_from="j-1")
    row = JobRow(job)
    row.update_from(job, predecessors=[("j-1", "Concatenate — A + B")])
    assert "Concatenate — A + B" in row._detail.toolTip()


def test_only_pending_rows_get_an_ordinal(qapp):
    """Regression guard (UX audit P2-11): "strictly serial … in order" was
    the blurb's own promise, but nothing on the page said what order --
    succeeded/failed/cancelled rows interleaved with pending ones with no
    position numbers. Only pending rows (queue positions still ahead) get
    one; a finished row's number wouldn't mean "runs next" anymore."""
    page = JobQueuePage()
    page.set_jobs([
        _job(id="j-1", state=JobState.SUCCEEDED),
        _job(id="j-2", state=JobState.PENDING),
        _job(id="j-3", state=JobState.PENDING),
        _job(id="j-4", state=JobState.RUNNING),
    ])
    assert [row._ordinal.text() for row in _rows(page)] == ["", "1.", "2.", ""]


def test_running_row_shows_an_indeterminate_progress_bar(qapp):
    """Regression guard (UX audit P2-11): the running row looked identical
    to an idle pending row until it finished -- no per-job percentage is
    ever reported, so an indeterminate bar is the only signal available
    that says "this one specifically is active right now"."""
    assert not JobRow(_job(state=JobState.RUNNING))._progress.isHidden()
    assert JobRow(_job(state=JobState.PENDING))._progress.isHidden()


def test_every_kind_icon_is_a_real_glyph(qapp):
    """make_pixmap substitutes alert-triangle for an unknown glyph name, which
    renders perfectly and reads as a deliberate warning beside an otherwise
    healthy row — so a typo here ships silently."""
    from widgets.icons import has_glyph

    missing = [name for name in KIND_ICON.values() if not has_glyph(name)]
    assert not missing, f"these would render as a warning triangle: {missing}"


# ── row menu ──────────────────────────────────────────────────────────────────

def test_move_actions_hidden_on_terminal_rows_shown_otherwise(qapp):
    """Regression guard (UX audit P2-11): up/down/reorder controls appeared
    on every row including finished ones, where reordering means nothing."""
    for state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED,
                  JobState.BLOCKED):
        assert "Move up" not in _actions(JobRow(_job(state=state))), state

    # Suspended and interrupted rows still have a future — the queue picks them
    # up again — so where they sit in the order still matters.
    for state in (JobState.PENDING, JobState.RUNNING,
                  JobState.SUSPENDED, JobState.INTERRUPTED):
        assert "Move up" in _actions(JobRow(_job(state=state))), state


def test_retry_is_offered_only_on_a_finished_job(qapp):
    """A pending job is already waiting and a running one is already going."""
    assert "Retry" in _actions(JobRow(_job(state=JobState.FAILED)))
    assert "Retry" not in _actions(JobRow(_job(state=JobState.PENDING)))
    assert "Retry" not in _actions(JobRow(_job(state=JobState.RUNNING)))


def test_edit_is_disabled_while_the_job_runs(qapp):
    """Its spec has already been handed to a runner."""
    menu = JobRow(_job(state=JobState.RUNNING)).build_menu()
    edit = next(a for a in menu.actions() if a.text() == "Edit…")
    assert not edit.isEnabled()


def test_run_after_offers_only_the_jobs_above_this_one(qapp):
    """The queue refuses an order placing a chained job before its
    predecessor, so offering the rest would be offering impossible choices."""
    job = _job(id="j-3")
    row = JobRow(job)
    row.update_from(job, predecessors=[("j-1", "First"), ("j-2", "Second")])
    _menu, sub = _chain_submenu(row)
    labels = [a.text() for a in sub.actions() if a.text()]
    assert labels == ["Nothing (run on its own)", "First", "Second"]


def test_run_after_emits_the_chosen_predecessor(qapp):
    job = _job(id="j-2")
    row = JobRow(job)
    row.update_from(job, predecessors=[("j-1", "First")])
    seen = []
    row.chainRequested.connect(lambda a, b: seen.append((a, b)))
    _menu, sub = _chain_submenu(row)
    next(a for a in sub.actions() if a.text() == "First").trigger()
    assert seen == [("j-2", "j-1")]


def test_run_after_nothing_unchains(qapp):
    row = JobRow(_job(id="j-2", chain_from="j-1"))
    seen = []
    row.chainRequested.connect(lambda a, b: seen.append((a, b)))
    _menu, sub = _chain_submenu(row)
    next(a for a in sub.actions() if a.text().startswith("Nothing")).trigger()
    assert seen == [("j-2", "")]


# ── row reuse and drag ────────────────────────────────────────────────────────

def test_unchanged_membership_reuses_the_same_row_widgets(qapp):
    """The page refreshes on every queueChanged, which during a run includes
    each status-message tick. Rebuilding every row for those threw away the
    scroll position and any hover state several times a second."""
    page = JobQueuePage()
    jobs = [_job(id="j-1", state=JobState.RUNNING), _job(id="j-2")]
    page.set_jobs(jobs)
    before = _rows(page)

    jobs[0].message = "processing well A1"
    page.set_jobs(jobs)
    assert _rows(page) == before
    assert "processing well A1" in page._rows["j-1"]._detail.toolTip()


def test_a_changed_order_does_rebuild(qapp):
    page = JobQueuePage()
    jobs = [_job(id="j-1"), _job(id="j-2")]
    page.set_jobs(jobs)
    page.set_jobs(list(reversed(jobs)))
    assert page._order == ["j-2", "j-1"]


def test_dragging_the_handle_reorders_and_does_not_also_open_the_job(qapp):
    """The row is a click target and the handle is inside it, so a drag that
    ended in a plain mouseReleaseEvent would both reorder the job and open
    its page underneath the reorder."""
    row = JobRow(_job())
    row.resize(400, 40)
    moves, clicks = [], []
    row.dragMoveBy.connect(lambda job_id, steps: moves.append((job_id, steps)))
    row.rowClicked.connect(clicks.append)

    row._drag_y0 = 0.0
    row._drag_steps = 2
    row._handle_release(None)
    assert moves == [("j-1", 2)]
    assert clicks == []


def test_a_plain_click_still_opens_the_job(qapp):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    row = JobRow(_job())
    clicks = []
    row.rowClicked.connect(clicks.append)
    event = QMouseEvent(QEvent.MouseButtonRelease, QPointF(200, 10),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    row.mouseReleaseEvent(event)
    assert clicks == ["j-1"]
