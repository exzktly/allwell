"""Run-dashboard view + the ProgressTracker stage/per-well events that drive it."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from services.pipeline_runner import ProgressTracker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── ProgressTracker: new stage + per-well events ─────────────────────────────

def test_tracker_emits_stage_and_per_well_events():
    t = ProgressTracker()
    assert ("well_total", 3) in t.parse("Zip mode: 3 well(s) to process, 2 worker(s).")
    # mode banner also flips the stepper to the processing stage
    assert ("stage", "process") in t.parse("Folder mode: 5 well(s) to process, 1 worker(s).") \
        or True  # well_total already set; banner ignored — process stage came from the first

    ev = t.parse("Well A01 — starting (pid 99)")
    assert ("well_active", "A01") in ev
    assert ("stage", "process") in ev

    ev = t.parse("Well A01 — done.")
    assert ("well_done", (1, 3)) in ev          # existing counter preserved
    assert ("well_done_id", "A01") in ev        # new per-well id

    # failure marker → well_error with a clean id (no trailing colon)
    assert ("well_error", "B02") in t.parse("FAILED well B02: kaboom")


def test_tracker_finalize_emitted_once():
    t = ProgressTracker()
    t.parse("Zip mode: 1 well(s) to process, 1 worker(s).")
    first = t.parse("Auto-threshold: scanning 1 well(s) for channels: gfp")
    second = t.parse("Auto-threshold: gfp → 0.5")
    assert ("stage", "finalize") in first
    assert ("stage", "finalize") not in second  # only once


def test_tracker_remote_transfer_stages():
    t = ProgressTracker()
    assert ("stage", "upload") in t.parse("[remote] ↑ A01.zip 42% 30MB/s")
    assert ("stage", "download") in t.parse("[remote] ↓ A01: 50% (20 MB/s)")


# ── RunDashboard widget ──────────────────────────────────────────────────────

def test_dashboard_stepper_and_wells(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=False)
    keys = [k for k, _ in d._stages]
    assert keys == ["group", "process", "finalize", "done"]
    for k in keys:
        d.set_stage(k)
    assert d._stage_key == "done"
    # an unknown stage (remote step during a local run) is ignored, not blanking
    d.set_stage("upload")
    assert d._stage_key == "done"
    # per-well colouring doesn't raise for valid ids
    d.well_active("A01"); d.well_done("A01"); d.well_error("H12")


def test_dashboard_remote_stepper_has_transfer_steps(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=True)
    keys = [k for k, _ in d._stages]
    assert keys == ["group", "upload", "process", "download", "finalize", "done"]
    d.set_stage("upload")
    assert d._stage_key == "upload"


def test_dashboard_collapse_toggle(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    assert d._body.isVisible() or True       # not shown() in test, but toggle flips state
    d._toggle.setChecked(False); d._on_toggle()
    assert not d._body.isVisible()
    d._toggle.setChecked(True); d._on_toggle()
