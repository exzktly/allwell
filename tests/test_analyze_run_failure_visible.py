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


_REFUSAL = (
    "Pipeline exited with code 2. 2026-08-08 19:10:32  ERROR     Not enough "
    "scratch space to process even one well: 4.9 GiB free.\n"
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
    assert tab._log.isVisible() or not tab.isVisible()


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
