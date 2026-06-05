"""Run-dashboard view + the per-well lifecycle events that drive it."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from services.pipeline_runner import ProgressTracker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── ProgressTracker: per-well lifecycle stage events ─────────────────────────

def _stages_for(tracker, line):
    return [p for k, p in tracker.parse(line) if k == "well_stage"]


def test_tracker_emits_full_per_well_lifecycle():
    t = ProgressTracker()
    t.parse("Zip mode: 3 well(s) to process, 2 worker(s).")
    assert ("A01", "upload") in _stages_for(t, "[remote] ↑ (1) A01.zip")
    assert ("A01", "active") in _stages_for(t, "Well A01 — starting (pid 9)")
    assert ("A01", "process") in _stages_for(t, "Well A01 — done.")
    # "download" requires BOTH the output zip AND the CSV(s) to have landed —
    # the zip alone (or ✓ materialised, which fires even on a CSV miss) is not
    # enough.
    assert ("A01", "download") not in _stages_for(t, "[remote] ↓ A01: 5.2 MB zip in 12s (3 Mbps)")
    assert ("A01", "download") not in _stages_for(
        t, "[remote] ✓ A01: materialised to /out (1/3 wells fetched)")
    assert ("A01", "download") in _stages_for(t, "[remote] ↓ A01: 1 CSV file(s) in 1s")
    assert ("A01", "threshold") in _stages_for(
        t, "Auto-threshold: completed well 1/3 (A01_out.zip)")
    assert ("A03", "error") in _stages_for(t, "FAILED well A03: boom")


def test_tracker_download_requires_both_zip_and_csv():
    # a well whose CSV pull failed (WARN, then materialised anyway) must NOT be
    # shown as downloaded — only the zip landed.
    t = ProgressTracker()
    t.parse("Zip mode: 2 well(s) to process, 1 worker(s).")
    assert ("B02", "download") not in _stages_for(t, "[remote] ↓ B02: 4.0 MB zip in 9s (3 Mbps)")
    assert ("B02", "download") not in _stages_for(
        t, "[remote] ↓ B02: WARN no CSV files found at /out/*_B02.csv")
    assert ("B02", "download") not in _stages_for(
        t, "[remote] ✓ B02: materialised to /out (2/2 wells fetched)")


def test_tracker_coarse_stage_and_finalize_once():
    t = ProgressTracker()
    t.parse("Zip mode: 1 well(s) to process, 1 worker(s).")
    up = t.parse("[remote] ↑ (1) A01.zip")
    assert ("stage", "upload") in up
    first = t.parse("Auto-threshold: scanning 1 well(s)")
    second = t.parse("Auto-threshold: completed well 1/1 (A01_out.zip)")
    assert ("stage", "finalize") in first
    assert ("stage", "finalize") not in second


# ── RunDashboard widget ──────────────────────────────────────────────────────

def test_dashboard_furthest_counts_and_tooltip(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=True, total=4)
    for well, stage in [("A01", "upload"), ("A02", "upload"), ("A01", "active"),
                        ("A01", "process"), ("A01", "download"),
                        ("A01", "threshold"), ("A03", "error")]:
        d.set_well_stage(well, stage)
    assert d._furthest == {"A01": "threshold", "A02": "upload", "A03": "error"}
    # furthest-based counts: reaching a later stage implies the earlier ones
    assert d._count_at_least(2) == 2          # uploaded (A01, A02)
    assert d._count_at_least(4) == 1          # processed (A01)
    assert d._count_at_least(6) == 1          # thresholded (A01)
    assert "complete" in d._well_tooltip("A01")
    assert "failed" in d._well_tooltip("A03")
    assert "pending" in d._well_tooltip("H12")
    # active well carries a glyph marker; cleared once it advances
    d2 = RunDashboard(); d2.reset(remote=False, total=1)
    d2.set_well_stage("A01", "active")
    assert d2._plate._grid._markers                      # gear set
    d2.set_well_stage("A01", "process")
    assert not d2._plate._grid._markers                  # cleared on advance


def test_dashboard_never_regresses(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard(); d.reset(remote=True, total=1)
    d.set_well_stage("A01", "threshold")
    d.set_well_stage("A01", "process")        # stale/out-of-order — ignored
    assert d._furthest["A01"] == "threshold"
    d.set_well_stage("A01", "error")          # error always wins
    assert d._furthest["A01"] == "error"


def test_dashboard_rail_local_vs_remote(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=False, total=8)
    assert list(d._rail) == ["group", "process", "threshold"]
    d.reset(remote=True, total=8)
    assert list(d._rail) == ["group", "upload", "process", "download", "threshold"]
    # active-stage highlight maps finalize -> threshold
    d.set_active_stage("finalize")
    assert d._active_stage == "threshold"


def test_dashboard_collapse_toggle(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d._toggle.setChecked(False); d._on_toggle()
    assert not d._body.isVisible()
    d._toggle.setChecked(True); d._on_toggle()
