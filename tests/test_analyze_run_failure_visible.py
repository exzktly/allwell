"""A failed Analyze run has to say so where the user is looking.

Reported: clicking Run showed progress reach *Grouped*, then the button dropped
straight back to idle as though nothing had started. ``pipeline_info.json`` was
written. No dialog, no warning, no visible reason. The pipeline had in fact
refused to start and said why — into a ``QTextEdit`` hidden behind the "Show raw
log" toggle, which is off by default.

Three things had to be true for a real failure to look like a no-op, and each
one is guarded here:

* the dashboard was never told, though it has had ``set_failed`` all along and
  every one of the six Datasets tools calls it — Analyze alone never did;
* the raw log stayed closed, so the one place the reason existed was the one
  place the user had no reason to open;
* ``_refresh_last_run_summary`` ran regardless of outcome, and its only test is
  that ``pipeline_info.json`` exists — which the pipeline writes *before* any
  image work. A run that died seconds in rendered a "Last run ·" card
  indistinguishable from a successful plate.

These assert the outbound state a user can see, not the private ``_run_error``:
the old code set that field correctly and still showed nothing.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


#: What ``pipeline_runner.exit_message`` produces for the reported refusal —
#: the reason first, the exit code as a trailing aside, because this text leads
#: a dashboard headline and the code is the part a user can do nothing with.
_REFUSAL = (
    "Not enough scratch space to process even one well: 4.9 GiB free on "
    "/Users/z/.allwell/scratch, and one well needs about 5.1 GiB of transient "
    "staging. Move the scratch or lower Workers. (pipeline exited with code 2)\n"
)


@pytest.fixture
def tab(qtbot):
    from analyze_tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    try:
        yield t
    finally:
        t._alive = False


def _fail_run(tab, message=_REFUSAL):
    """Drive the tab's own event handling for a run that failed."""
    tab._run_ok = False
    tab._run_error = None
    # Feed the two events the runner emits, through the tab's real handler.
    tab._log_q.put(("error", message))
    tab._log_q.put(("finished", None))
    tab._poll_log()


def test_a_failed_run_puts_the_reason_on_the_dashboard(tab):
    _fail_run(tab)
    headline = tab._dashboard._headline.text()
    assert "Failed" in headline, headline
    assert "scratch space" in headline, headline


def test_the_raw_log_is_open_before_anything_has_run(tab):
    """The log carries things nothing else says — the reason a run refused to
    start, the volume that filled, where the errors/ folder is — so it is open
    and the toggle is there to put it away.

    isVisibleTo, not isVisible: the tab is never shown in these tests, so
    isVisible() is False for every widget in it and an assertion built on it
    would hold whether the log were revealed or not.
    """
    assert tab._raw_log_cb.isChecked() is True
    assert tab._log.isVisibleTo(tab) is True
    assert tab._log_sep.isVisibleTo(tab) is True

    # And it is genuinely a toggle, not a fixture.
    tab._raw_log_cb.setChecked(False)
    assert tab._log.isVisibleTo(tab) is False
    assert tab._log_sep.isVisibleTo(tab) is False


def test_a_failed_run_reveals_the_raw_log(tab):
    """The reason is written into a pane the user can close. Re-opening it on
    failure is what turns "nothing happened" into an explanation.

    The pane is open at build, so the precondition below stands in for the user
    having put it away — without it `force_raw_log_open` returns early on an
    already-ticked box and the assertion passes while measuring nothing.
    """
    tab._raw_log_cb.setChecked(False)                # the user closed it
    _fail_run(tab)
    assert tab._raw_log_cb.isChecked() is True
    # isVisibleTo, not isVisible: the tab is never shown in this test, so
    # isVisible() is False for every widget in it and any assertion built on it
    # holds whether the log was revealed or not.
    assert tab._log.isVisibleTo(tab) is True


def test_a_failed_run_does_not_paint_a_last_run_card(tab, tmp_path, monkeypatch):
    """The card's only test is that the sidecar exists, and the pipeline writes
    that before any image work — so on failure it described a run that never
    produced anything."""
    called = []
    monkeypatch.setattr(type(tab), "_refresh_last_run_summary",
                        lambda self: called.append(1), raising=False)
    _fail_run(tab)
    assert called == []


def test_a_successful_run_still_paints_it(tab, monkeypatch):
    """The other half of the gate. Characterisation, not a guard: this passes
    against the pre-fix code too, where the card was painted unconditionally.
    It exists so a future tightening of the gate cannot silently take the card
    away from the runs that should have it."""
    called = []
    monkeypatch.setattr(type(tab), "_refresh_last_run_summary",
                        lambda self: called.append(1), raising=False)
    tab._run_ok = True
    tab._run_error = None
    tab._log_q.put(("finished", None))
    tab._poll_log()
    assert called == [1]


# ── a cancel is not a failure ────────────────────────────────────────────────
#
# ``stop()`` and ``suspend()`` both end the subprocess by signal, so both arrive
# as an ("error", …) event carrying a signal exit code —
# ``PipelineRunner.was_stopped``'s own docstring says the three are
# indistinguishable from outside, which is why it exists. Painting a deliberate
# Stop red, and quoting whatever line the pipeline happened to be on as its
# "reason", is a worse lie than saying nothing.

def _end_run(tab, *, stopped=False, suspended=False, message=_REFUSAL):
    if stopped:
        tab._runner._stop_requested.set()
    if suspended:
        tab._runner._suspend_requested.set()
    # The runner snapshots these when it queues the failure; this stands in for
    # the enqueue the real runner would have done.
    tab._runner._error_was_stopped = tab._runner._stop_requested.is_set()
    tab._runner._error_was_suspended = tab._runner._suspend_requested.is_set()
    tab._run_ok = False
    tab._run_error = None
    tab._log_q.put(("error", message))
    tab._log_q.put(("finished", None))
    tab._poll_log()


def test_a_stopped_run_reads_as_cancelled_not_failed(tab):
    _end_run(tab, stopped=True)
    headline = tab._dashboard._headline.text()
    assert headline.startswith("Cancelled"), headline
    assert "Failed" not in headline


def test_a_suspended_run_reads_as_cancelled_not_failed(tab):
    _end_run(tab, suspended=True)
    headline = tab._dashboard._headline.text()
    assert headline.startswith("Cancelled"), headline
    assert "resume" in headline.lower(), headline


def test_a_cancel_does_not_force_the_log_open(tab):
    """The user asked for this and knows why; opening a pane they had closed
    would be the app arguing with them.

    Characterisation, not a guard: it passes against the pre-fix code, which
    never opened the pane for anything. It pins the exemption so the failure
    path added beside it cannot later grow to cover cancels.
    """
    tab._raw_log_cb.setChecked(False)                # the user closed it
    _end_run(tab, stopped=True)
    assert tab._raw_log_cb.isChecked() is False


def test_a_long_reason_is_cut_for_the_headline_and_kept_on_the_tooltip(tab):
    """The disk refusal runs to hundreds of characters — two volumes, two
    figures, two remedies. A one-line label beside a progress plate is not
    where that belongs, and pasting it there grows the dashboard instead."""
    long_reason = (
        "Not enough scratch space to process even one well: 4.9 GiB free. "
        + "Move the scratch to a roomier volume, or lower Workers. " * 6
    )
    _end_run(tab, message=long_reason)
    headline = tab._dashboard._headline.text()
    assert len(headline) < len(long_reason)
    assert "Not enough scratch space" in headline
    # Nothing is lost — the whole sentence is still reachable.
    assert "lower Workers" in tab._dashboard._headline.toolTip()


def test_a_refusal_unfolds_the_section_it_names(tab, tmp_path, monkeypatch):
    """Naming a remedy the user cannot see is the same dead end as not naming
    one. The disk check points at Compute Options, which folds by default.

    The message comes from the real ``preflight_scratch_space`` rather than
    being written out here. The whole mechanism is a text match between what
    `pipeline/capacity.py` writes and the form's own section titles, so a
    hand-copied message would leave the test green through a reword of either
    side — the one failure this guard exists to catch.
    """
    import pipeline.capacity as cap

    # 4.9 GiB free, as reported. The volume this test runs on has plenty, so
    # the shortfall is imposed rather than found.
    monkeypatch.setattr(cap, "free_bytes", lambda _p: int(4.9 * 1024 ** 3))
    ok, message = cap.preflight_scratch_space(
        tmp_path,
        sample_image_bytes=64 * 1024 * 1024,
        n_channels=5, groups_per_well=10, concurrent_wells=4,
    )
    assert ok is False, "fixture no longer produces the refusal branch"

    folded_before = {t: b.isVisibleTo(tab)
                     for t, (b, _c) in tab._collapsible_sections.items()}
    assert folded_before["Compute Options"] is False
    _end_run(tab, message=message)
    assert tab._collapsible_sections["Compute Options"][0].isVisibleTo(tab) is True
    # And nothing it did not name: the message points at Compute Options and
    # the scratch field, not at every folded section on the form.
    unfolded = {t for t, (b, _c) in tab._collapsible_sections.items()
                if b.isVisibleTo(tab) and not folded_before[t]}
    assert all(t.lower() in message.lower() for t in unfolded), unfolded


def test_only_the_first_error_reaches_the_dashboard(tab):
    """A dying pipeline can emit more than one error line, and the first is the
    cause — everything after it is that cause's fallout. ``_run_error`` latches
    the first, so painting a later one would leave the headline naming
    something other than the message ``runFinished`` carries."""
    tab._run_ok = False
    tab._run_error = None
    tab._log_q.put(("error", "Not enough scratch space to process even one well.\n"))
    tab._log_q.put(("error", "Cleanup failed: scratch directory already gone.\n"))
    tab._log_q.put(("finished", None))
    seen = []
    tab.runFinished.connect(lambda ok, msg: seen.append(msg))
    tab._poll_log()
    headline = tab._dashboard._headline.text()
    assert "scratch space" in headline, headline
    assert "Cleanup failed" not in headline
    assert seen and "scratch space" in seen[0]


def test_the_next_run_hands_the_raw_log_toggle_back(tab):
    """Revealing the log is a change to a setting the user chose. It is made on
    their behalf so a failure can be read, not kept: the next run restores it.

    This and the test below pin new API. Run against ``origin/main`` they fail
    on their own first assertion — that a failure opened the pane at all —
    which is what ``test_a_failed_run_reveals_the_raw_log`` already proves, so
    neither has ever been red for the behaviour it names.
    """
    tab._raw_log_cb.setChecked(False)                # the user closed it
    _fail_run(tab)
    assert tab._raw_log_cb.isChecked() is True
    tab._set_running_ui_state()
    assert tab._raw_log_cb.isChecked() is False


def test_a_toggle_the_user_worked_themselves_is_not_taken_back(tab):
    """Once they have opened it deliberately, it is theirs — a later run must
    not close a pane the user asked for. See the note above on what this can
    and cannot prove against the pre-fix code."""
    tab._raw_log_cb.setChecked(False)                # the user closed it
    _fail_run(tab)
    assert tab._raw_log_cb.isChecked() is True
    tab._raw_log_cb.setChecked(False)      # the user closes it …
    tab._raw_log_cb.setChecked(True)       # … then opens it on purpose
    tab._set_running_ui_state()
    assert tab._raw_log_cb.isChecked() is True


def test_a_stop_clicked_after_the_failure_does_not_hide_it(tab):
    """The reported bug, reintroduced by a race, if the cancel test ran live.

    A genuine failure queues its ``("error", …)`` event; the GUI drains one
    timer tick later. A Stop clicked in that window sets the runner's live
    ``was_stopped``, and reading it at drain time paints amber "Cancelled",
    leaves the raw log shut and never states the reason. The runner snapshots
    the answer when it queues the failure instead.
    """
    tab._raw_log_cb.setChecked(False)                # the user closed it
    tab._run_ok = False
    tab._run_error = None
    # Failure queued while nothing was cancelled …
    tab._runner._error_was_stopped = False
    tab._runner._error_was_suspended = False
    tab._log_q.put(("error", _REFUSAL))
    # … then the user hits Stop before the next poll.
    tab._runner._stop_requested.set()
    tab._log_q.put(("finished", None))
    tab._poll_log()

    headline = tab._dashboard._headline.text()
    assert headline.startswith("Failed"), headline
    assert tab._raw_log_cb.isChecked() is True


def test_an_unrelated_failure_leaves_the_sections_folded(tab):
    """It unfolds what the message names and nothing else — a segmentation
    crash has no business rearranging the form.

    Characterisation, not a guard: it passes against the pre-fix code, which
    unfolded nothing at all. Its job is to bound the mechanism the test above
    introduces, and it bites on any widening of the match.
    """
    folded_before = {t: b.isVisibleTo(tab)
                     for t, (b, _c) in tab._collapsible_sections.items()}
    _end_run(tab, message="StarDist failed on well A01: out of memory.")
    folded_after = {t: b.isVisibleTo(tab)
                    for t, (b, _c) in tab._collapsible_sections.items()}
    assert folded_after == folded_before
