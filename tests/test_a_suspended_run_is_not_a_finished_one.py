"""A suspended run is reported as suspended, never as a finished plate.

``pipeline.suspend`` makes a suspended pipeline leave through its normal
end-of-well path, so it exits **0**. The runner read every exit of 0 as "the
plate is done" and queued ``("done", "Pipeline completed successfully.")``, and
from there the whole completion path ran against a half-finished plate:

* the tab printed "Processing Complete" and set ``_run_ok``;
* ``_on_pipeline_complete`` handed the output folder to Review, whose load ran
  inside a ``QTimer`` callback in ``allwell.py`` — the crash reported as
  "complained about the location of the timer";
* ``jobFinished`` said ``ok=True``, so the job queue recorded the job as
  finished instead of keeping it to resume;
* the success hooks that tidy up source images ran.

These drive the real ``PipelineRunner`` against a stand-in pipeline that
behaves the way the real one does on SIGTERM (finish the well in hand, then
exit 0), and the real ``AnalyzeTab`` through its real Suspend button.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Announces wells like the real pipeline and, like ``pipeline.suspend``,
#: treats SIGTERM as "finish the well in hand, start no more, exit 0".
_FAKE_PIPELINE = '''
import signal, sys, time
wells = sys.argv[1].split(",")
delay = float(sys.argv[2])
stop = []
signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
print(f"Zip mode: {len(wells)} wells", flush=True)
for w in wells:
    if stop:
        break
    print(f"Well {w} F001 starting", flush=True)
    time.sleep(delay)
    print(f"Well {w} F001 done.", flush=True)
'''

_WELLS = ["A01", "A02", "A03", "A04", "A05", "A06"]


@pytest.fixture
def fake_pipeline(tmp_path, monkeypatch):
    script = tmp_path / "fake_pipeline.py"
    script.write_text(_FAKE_PIPELINE)
    import services.pipeline_runner as pr

    monkeypatch.setattr(
        pr, "build_pipeline_args",
        lambda pipeline, i, o, opts: [sys.executable, str(script),
                                      ",".join(_WELLS), str(PER_WELL_S)])
    return script


#: Seconds per well. Long enough by default for a test to press Suspend while
#: the first well is running; a test that never suspends shortens it.
PER_WELL_S = 0.6


def _drain(runner, until_kind: str, timeout: float = 20.0) -> list[str]:
    kinds: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            kind, _payload = runner.log_q.get(timeout=0.1)
        except Exception:
            continue
        kinds.append(kind)
        if kind == until_kind:
            return kinds
    raise AssertionError(f"no {until_kind!r} event within {timeout}s; saw {kinds}")


def _wait_for_line(runner, text: str, seen: list[str], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            kind, payload = runner.log_q.get(timeout=0.1)
        except Exception:
            continue
        seen.append(kind)
        if kind == "line" and text in str(payload):
            return
    raise AssertionError(f"the pipeline never said {text!r}")


def test_the_runner_reports_a_suspend_that_exits_zero_as_suspended(
        tmp_path, fake_pipeline):
    from services.pipeline_runner import PipelineRunner

    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    cleaned: list[bool] = []

    runner = PipelineRunner()
    runner.start(
        fake_pipeline,
        {"filename_schema": "experiment:channel:well:fov:timepoint",
         "filename_sep": "_"},
        resolve_dirs=lambda opts, log_q, **kw: (
            in_dir, out_dir, lambda: cleaned.append(True)),
        journal_spec={"opts": {}},
    )
    seen: list[str] = []
    _wait_for_line(runner, "Well A01 F001 starting", seen)
    runner.suspend()
    seen += _drain(runner, "finished")

    assert "suspended" in seen, f"a suspend that exited 0 was not reported as one: {seen}"
    assert "done" not in seen, (
        "a suspended run was reported as a completed plate, which is what sent "
        "the half-written folder to Review and marked the queue job finished")
    assert "error" not in seen
    assert not cleaned, "the success hook ran for a run that did not finish"


def test_a_run_that_finishes_is_still_done(tmp_path, fake_pipeline, monkeypatch):
    """The other side of the split: an exit of 0 without Suspend is a completion."""
    from services.pipeline_runner import PipelineRunner

    monkeypatch.setattr(sys.modules[__name__], "PER_WELL_S", 0.05)

    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    cleaned: list[bool] = []
    runner = PipelineRunner()
    runner.start(
        fake_pipeline,
        {"filename_schema": "experiment:channel:well:fov:timepoint",
         "filename_sep": "_"},
        resolve_dirs=lambda opts, log_q, **kw: (
            in_dir, out_dir, lambda: cleaned.append(True)),
        journal_spec={"opts": {}},
    )
    seen = _drain(runner, "finished")
    assert "done" in seen and "suspended" not in seen, seen
    assert cleaned == [True]


@pytest.fixture
def tab(qtbot, monkeypatch, tmp_path, fake_pipeline):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    monkeypatch.setattr("services.suspend_record.record_path",
                        lambda: tmp_path / "state" / "suspended.json")
    from analyze.tab import AnalyzeTab

    handed_to_review: list[object] = []
    t = AnalyzeTab(on_pipeline_complete=handed_to_review.append)
    qtbot.addWidget(t)
    t.handed_to_review = handed_to_review
    return t


def test_pressing_suspend_does_not_hand_a_half_finished_plate_to_review(
        tab, qtbot, tmp_path, fake_pipeline):
    """The reported crash, one step before Review: the hand-off must not happen."""
    from services.run_spec import RunSpec

    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    out_dir = plate / "out"
    out_dir.mkdir()
    finished: list[tuple] = []
    tab.jobFinished.connect(lambda *a: finished.append(a))

    def _resolve(opts, log_q, **kw):
        return (plate / "in", out_dir, None)

    tab._resolve_run_dirs_for_runner = _resolve
    spec = RunSpec.from_json({"opts": {
        "raw": str(plate),
        "filename_schema": "experiment:channel:well:fov:timepoint",
        "filename_sep": "_", "nuclear_token": "DAPI", "fluor_tokens": ["GFP"]}})
    assert tab.start_run(spec, pipeline=fake_pipeline) is None

    qtbot.waitUntil(lambda: "Well A01 F001 starting" in tab._log.toPlainText(),
                    timeout=10_000)
    tab._suspend_btn.click()
    qtbot.waitUntil(lambda: bool(finished), timeout=20_000)

    log = tab._log.toPlainText()
    assert tab.handed_to_review == [], (
        "Suspend handed the output folder to Review as a finished dataset; the "
        "Review load runs in a QTimer callback and took the window down")
    assert "Processing Complete" not in log
    assert "[Suspended]" in log
    assert tab._run_ok is False
    ok, message, out = finished[0]
    assert (ok, message, out) == (False, "", ""), (
        "the queue recognises a suspend by ok=False with no error message; "
        f"got {finished[0]!r}")


def test_a_review_load_that_raises_does_not_escape_the_timer(qtbot, caplog):
    """The hand-off itself: whatever Review does, the callback must not raise."""
    import logging

    import allwell

    class _Review:
        def _load_path(self, path):
            raise RuntimeError(f"cannot read {path}")

    win = allwell.AllWellApp.__new__(allwell.AllWellApp)
    win._review = _Review()
    with caplog.at_level(logging.WARNING, logger="review"):
        win._load_in_review_later("/nowhere", delay_ms=0)
        qtbot.waitUntil(
            lambda: any("loading /nowhere into Review failed" in r.getMessage()
                        for r in caplog.records),
            timeout=5_000)
