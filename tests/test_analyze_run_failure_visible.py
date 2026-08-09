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


def test_a_failed_run_reveals_the_raw_log(tab):
    """The reason is written into a pane that is closed by default. Opening it
    on failure is what turns "nothing happened" into an explanation."""
    assert tab._raw_log_cb.isChecked() is False      # closed at build
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
    would be the app arguing with them."""
    assert tab._raw_log_cb.isChecked() is False
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
    assert "lower Workers" in tab._dashboard.toolTip()


def test_a_refusal_unfolds_the_section_it_names(tab):
    """Naming a remedy the user cannot see is the same dead end as not naming
    one. The disk check points at Compute Options, which folds by default."""
    body, _caret = tab._collapsible_sections["Compute Options"]
    assert body.isVisibleTo(tab) is False
    _end_run(tab, message=(
        "Not enough scratch space to process even one well: 4.9 GiB free. "
        "To start anyway, use --allow_low_disk (Analyze → Compute Options → "
        "“Start even if the disk looks too small”). (pipeline exited with code 2)"))
    assert body.isVisibleTo(tab) is True


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
    their behalf so a failure can be read, not kept: the next run restores it."""
    assert tab._raw_log_cb.isChecked() is False
    _fail_run(tab)
    assert tab._raw_log_cb.isChecked() is True
    tab._set_running_ui_state()
    assert tab._raw_log_cb.isChecked() is False


def test_a_toggle_the_user_worked_themselves_is_not_taken_back(tab):
    """Once they have opened it deliberately, it is theirs — a later run must
    not close a pane the user asked for."""
    _fail_run(tab)
    assert tab._raw_log_cb.isChecked() is True
    tab._raw_log_cb.setChecked(False)      # the user closes it …
    tab._raw_log_cb.setChecked(True)       # … then opens it on purpose
    tab._set_running_ui_state()
    assert tab._raw_log_cb.isChecked() is True


def test_an_unrelated_failure_leaves_the_sections_folded(tab):
    """It unfolds what the message names and nothing else — a segmentation
    crash has no business rearranging the form."""
    folded_before = {t: b.isVisibleTo(tab)
                     for t, (b, _c) in tab._collapsible_sections.items()}
    _end_run(tab, message="StarDist failed on well A01: out of memory.")
    folded_after = {t: b.isVisibleTo(tab)
                    for t, (b, _c) in tab._collapsible_sections.items()}
    assert folded_after == folded_before
