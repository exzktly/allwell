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

def test_dashboard_per_stage_counts_and_tooltip(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=True, total=4)
    for well, stage in [("A01", "upload"), ("A02", "upload"), ("A01", "active"),
                        ("A01", "process"), ("A01", "download"),
                        ("A01", "threshold"), ("A03", "error")]:
        d.set_well_stage(well, stage)
    # stages tracked independently — each counts only the wells that hit it
    assert d._count("upload") == 2            # A01, A02
    assert d._count("process") == 1           # A01
    assert d._count("download") == 1          # A01
    assert "fetched" in d._well_tooltip("A01")
    assert "failed" in d._well_tooltip("A03")
    assert "pending" in d._well_tooltip("H12")
    # active well carries a glyph marker; cleared once it advances
    d2 = RunDashboard(); d2.reset(remote=False, total=1)
    d2.set_well_stage("A01", "active")
    assert d2._plate._grid._markers                      # gear set
    d2.set_well_stage("A01", "process")
    assert not d2._plate._grid._markers                  # cleared on advance


def test_remote_threshold_does_not_count_as_download(qapp):
    # the bug: on a remote run the server thresholds before the client downloads;
    # "threshold" must NOT make Download read N/N before anything is fetched.
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard(); d.reset(remote=True, total=3)
    for w in ("A01", "A02", "A03"):
        d.set_well_stage(w, "process")
        d.set_well_stage(w, "threshold")      # server finished all wells
    assert d._count("threshold") == 3
    assert d._count("download") == 0          # nothing fetched yet
    from ui.theme import ACCENT, CLR_SUCCESS
    assert d._well_color("A01") == ACCENT      # blue: computed, awaiting fetch
    d.set_well_stage("A01", "download")
    assert d._count("download") == 1
    assert d._well_color("A01") == CLR_SUCCESS  # green only once data is local


def test_dashboard_error_wins(qapp):
    from widgets.run_dashboard import RunDashboard
    from ui.theme import CLR_DANGER
    d = RunDashboard(); d.reset(remote=True, total=1)
    d.set_well_stage("A01", "process")
    d.set_well_stage("A01", "error")
    assert d._well_color("A01") == CLR_DANGER and "failed" in d._well_tooltip("A01")


def test_dashboard_rail_local_vs_remote(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d.reset(remote=False, total=8)
    assert list(d._rail) == ["group", "process", "threshold"]
    d.reset(remote=True, total=8)
    # remote order: server thresholds before the client downloads
    assert list(d._rail) == ["group", "upload", "process", "threshold", "download"]
    # active-stage highlight maps finalize -> threshold
    d.set_active_stage("finalize")
    assert d._active_stage == "threshold"


def test_dashboard_collapse_toggle(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard()
    d._toggle.setChecked(False); d._on_toggle()
    assert not d._body.isVisible()
    d._toggle.setChecked(True); d._on_toggle()


def test_tracker_emits_per_well_fov_progress():
    t = ProgressTracker()
    assert ("A01", 0, 12) in [p for k, p in t.parse("Well A01 — 12 FOV-group(s)")
                              if k == "well_fov"]
    assert ("A01", 3, 12) in [p for k, p in t.parse("Well A01 — FOV 3/12 done")
                              if k == "well_fov"]


def test_dashboard_fov_sub_progress(qapp):
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard(); d.reset(remote=True, total=2)
    d.set_well_stage("A01", "active"); d.set_well_stage("B02", "active")
    d.set_well_fov("A01", 0, 12); d.set_well_fov("B02", 0, 8)
    d.set_well_fov("A01", 7, 12)
    txt = d._fov_lbl.text()
    assert "A01 · FOV 7/12" in txt and "B02 · FOV 0/8" in txt
    # a well that finishes processing drops out of the "currently computing" line
    d.set_well_stage("A01", "process")
    assert "A01" not in d._fov_lbl.text() and "B02" in d._fov_lbl.text()
