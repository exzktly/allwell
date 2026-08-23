"""One test that actually runs a run, and asks the journal what it saw.

Every other journal test on this branch calls ``run_journal``'s API directly,
in whatever order its author had in mind. That is the wrong altitude for this
record, because the record has **two writers on two threads and no ordering
between them**:

* ``PipelineRunner._run_pipeline_thread`` opens the journal, and closes it in
  its ``finally`` the instant the pipeline subprocess exits — on the **worker**
  thread;
* ``analyze_progress._journal_well_stage`` writes ``analysed`` when the
  ``Well X … done.`` line is *parsed*, which happens on the **GUI** thread as
  ``AnalyzeTab._poll_log`` drains ``log_q`` in bounded 15 ms slices.

So the queue still holds unparsed completion lines at the moment the run is
recorded as closed, and every rule about "may this be written" has to hold for
lines that arrive after the run has ended. A rule that reads correct against
the module API can silently drop the tail of every plate — which is exactly
what happened, and what no API-level test could see (CLAUDE.md §7.7 #1).

What this drives is the real ``PipelineRunner``, the real ``ProgressTracker``,
and a faithful copy of ``_poll_log``'s bounded drain. The pipeline itself is a
script that prints the same markers the real one does, so the run is real from
the runner's point of view and takes a second rather than an hour.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from analyze_tab import _POLL_BUDGET_S, _POLL_MAX_LINES        # noqa: E402
from services import run_journal as J                          # noqa: E402
from services.pipeline_runner import PipelineRunner, ProgressTracker  # noqa: E402

#: A stand-in pipeline that announces wells the way the real one does and then
#: exits. ``--per-well`` paces it, so the queue can be made to lag the way it
#: does under a real segmentation load.
_FAKE_PIPELINE = '''
import sys, time
wells = sys.argv[1].split(",")
delay = float(sys.argv[2])
print(f"Zip mode: {len(wells)} wells", flush=True)
for w in wells:
    print(f"Well {w} F001 starting", flush=True)
    time.sleep(delay)
    print(f"Well {w} F001 done.", flush=True)
print("Run complete.", flush=True)
'''


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")


def _drain_like_the_gui(runner, tracker, out_dir, journal_stage, events) -> None:
    """One slice of ``AnalyzeTab._poll_log``, bounded exactly as it is.

    The bound is the point. A drain that ran until the queue was empty would
    never reproduce the lag, and the real one cannot: it is on the GUI thread
    and re-arms between slices.
    """
    deadline = time.monotonic() + _POLL_BUDGET_S
    seen = 0
    while True:
        if time.monotonic() >= deadline or seen >= _POLL_MAX_LINES:
            return
        try:
            kind, payload = runner.log_q.get_nowait()
        except Exception:
            return
        seen += 1
        events.append((kind, payload))
        if kind != "line":
            continue
        for ev_kind, ev_payload in tracker.parse(payload):
            if ev_kind == "well_stage":
                well, stage = ev_payload
                journal_stage(str(well), str(stage))


def _run_a_plate(tmp_path, wells, *, per_well: float):
    """Start the real runner against the fake pipeline; return the journal."""
    script = tmp_path / "fake_pipeline.py"
    script.write_text(_FAKE_PIPELINE)
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    runner = PipelineRunner()
    tracker = ProgressTracker()
    recorded: list[tuple[str, bool]] = []
    _seen_events: list[tuple[str, object]] = []

    def journal_stage(well: str, stage: str) -> None:
        # What `analyze_progress._journal_well_stage` does, minus the widget.
        state = {"process": "analysed", "error": "failed"}.get(stage)
        if state is None:
            return
        recorded.append((well, bool(J.record_well(out_dir, well, state))))

    def _resolve(opts, log_q, **kw):
        return (in_dir, out_dir, None)

    def _build(pipeline, i, o, opts):
        return [sys.executable, str(script), ",".join(wells), str(per_well)]

    import services.pipeline_runner as pr
    real_build = pr.build_pipeline_args
    pr.build_pipeline_args = _build
    try:
        runner.start(script, {"filename_schema": "well_fov_channel",
                              "filename_sep": "_"},
                     resolve_dirs=_resolve, journal_spec={"opts": {}})
        # Until the runner says it has finished — not until the queue looks
        # empty, which it does for a moment before the worker thread starts.
        deadline = time.time() + 60
        while time.time() < deadline:
            _drain_like_the_gui(runner, tracker, out_dir, journal_stage,
                                _seen_events)
            if any(k == "finished" for k, _ in _seen_events):
                break
            time.sleep(0.001)
        # The run has ended and the GUI keeps draining what is left, exactly as
        # it does — which is the whole point: those lines arrive *after* the
        # worker thread's `finally` recorded the run as closed.
        for _ in range(500):
            if runner.log_q.empty():
                break
            _drain_like_the_gui(runner, tracker, out_dir, journal_stage,
                                _seen_events)
        assert any(k == "finished" for k, _ in _seen_events), (
            "the run never reported finishing")
    finally:
        pr.build_pipeline_args = real_build

    errors = [p for k, p in _seen_events if k == "error"]
    assert not errors, f"the run failed: {errors}"
    return J.read_journal(out_dir), recorded, tracker


WELLS = [f"{r}{c:02d}" for r in "AB" for c in range(1, 13)]      # 24 wells


@pytest.mark.parametrize("per_well", [0.0, 0.002])
def test_the_journal_names_every_well_the_run_announced(tmp_path, per_well):
    """The claim the whole record exists to support.

    A plate whose pipeline announced 24 wells must produce a journal naming 24.
    Losing the tail is invisible in every other way: the CSVs are all on disk,
    the run reports success, and only the pre-run readout and the resume
    banner — which read `RunJournal.tally()` — quietly say 23.
    """
    journal, recorded, tracker = _run_a_plate(tmp_path, WELLS, per_well=per_well)

    assert tracker.well_done == len(WELLS), (
        f"the pipeline announced {tracker.well_done} of {len(WELLS)} wells; "
        "the fixture is not exercising a whole plate")
    assert journal is not None, "the run left no journal at all"

    refused = [w for w, ok in recorded if not ok]
    assert not refused, (
        f"{len(refused)} well(s) were refused by the journal: {refused}. "
        "These are completion lines still queued when the worker thread closed "
        "the record — the tail of the plate, on every run.")
    assert set(journal.wells) == set(WELLS), (
        f"the journal names {len(journal.wells)} of {len(WELLS)} wells; "
        f"missing {sorted(set(WELLS) - set(journal.wells))}")


def test_the_tally_the_user_is_shown_matches_the_plate(tmp_path):
    """Asserted at the surface, not at the record (CLAUDE.md §7.4 #1).

    ``tally()`` is what the pre-run readout and the resume banner render, so
    this is the sentence a user actually reads after a run.
    """
    journal, _recorded, _tracker = _run_a_plate(tmp_path, WELLS, per_well=0.0)

    assert journal is not None
    assert f"{len(WELLS)} finished" in journal.tally(), (
        f"the readout would tell the user: {journal.tally()!r}")
