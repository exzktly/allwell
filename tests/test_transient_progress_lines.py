"""Transfer progress is live state, so it does not go in the log.

A remote transfer used to write a line every few seconds — bytes so far, time
elapsed — plus rsync's own percentage stream once a second. A log records things
that *happened*, and "still transferring" is a sample of a continuous variable,
not an event. Worse, it evicted the record it was crowding: the log holds 10 000
blocks, and ``analyze_tab._log_line`` carries a block of scroll-compensation
whose own comment blames this stream for making that trimming common.

Those lines now carry ``TRANSIENT_PREFIX`` and are routed to the dashboard's
status line instead. The log keeps the events — the start, the per-well names,
the final "X MB in Ys" — plus one durable breadcrumb a minute, because a status
line cannot answer "was it stuck at 3am?" the following morning.

The prefix is applied at the source rather than sniffed downstream: the per-file
``[remote] ↑ <well>.zip`` names look similar and are real events driving the
Upload rail, so guessing between the two by shape would be fragile.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from services.pipeline_runner import (
    TRANSIENT_PREFIX,
    ProgressTracker,
    is_transient_progress,
)


def test_a_marked_line_is_transient():
    assert is_transient_progress(f"{TRANSIENT_PREFIX}Downloading C02 — 4.1 MB, 12s")


@pytest.mark.parametrize("line", [
    "[remote] ↑ C02.zip on remote",
    "[remote] ↑ (3) D04.zip",
    "[remote] ↓ C02: starting download from cluster",
    "[remote] upload: 143.7 MB in 2m 14s",
    "Well C02 — done.",
])
def test_real_events_are_not_transient(line):
    """Everything the log exists to keep must stay out of this bucket."""
    assert not is_transient_progress(line)


def test_a_transient_line_becomes_a_status_update():
    tracker = ProgressTracker()
    events = tracker.parse(f"{TRANSIENT_PREFIX}Downloading C02 — 4.1 MB, 12s")
    assert events == [("status", "Downloading C02 — 4.1 MB, 12s")]


def test_a_transient_line_yields_nothing_else():
    """It must not also count as a stage, a well event, or a log line."""
    tracker = ProgressTracker()
    events = tracker.parse(f"{TRANSIENT_PREFIX}[remote] ↑ C02.zip 45%")
    assert [k for k, _ in events] == ["status"]


def test_the_upload_rail_still_reads_the_per_file_names():
    """The regression this guards: marking rsync's stream transient must not
    take the per-well name lines with it — those are what fill the rail."""
    tracker = ProgressTracker()
    events = tracker.parse("[remote] ↑ (3) D04.zip")
    assert ("well_stage", ("D04", "upload")) in events


def test_the_gui_skips_transient_lines(qapp):
    """The wiring: ``_poll_log`` must consult the predicate before logging."""
    import inspect

    from analyze_tab import AnalyzeTab

    src = inspect.getsource(AnalyzeTab._poll_log)
    assert "is_transient_progress(payload)" in src
    assert "if not is_transient_progress(payload):" in src


def test_the_heartbeat_emits_both_cadences():
    """A fast live sample and a slow durable breadcrumb, from one loop."""
    import inspect

    from services.remote_runner import Fetcher

    src = inspect.getsource(Fetcher._transfer_heartbeat)
    assert "TRANSIENT_PREFIX" in src, "the live sample must be marked transient"
    assert "_BREADCRUMB_INTERVAL_S" in src, "the log still gets a slow breadcrumb"
    assert "still transferring" in src


def test_rsync_progress_no_longer_goes_to_the_log():
    import inspect

    import services.remote_runner as rr

    src = inspect.getsource(rr._run_rsync_streaming)
    assert "TRANSIENT_PREFIX}{line}" in src
    assert 'log_fn(f"[remote] ↑ {line}' not in src


def test_the_breadcrumb_is_a_minute_not_seconds():
    from services.remote_runner import _BREADCRUMB_INTERVAL_S

    assert _BREADCRUMB_INTERVAL_S >= 60.0
