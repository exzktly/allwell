"""Run-dashboard view + the per-well lifecycle events that drive it."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services.pipeline_runner import ProgressTracker


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
    # A computing well is marked by its dial and its colour, not by a glyph:
    # the ⚙ used to sit in the middle of exactly the area the dial now fills.
    # Only an error still carries a marker.
    d2 = RunDashboard(); d2.reset(remote=False, total=1)
    d2.set_well_stage("A01", "active")
    assert not d2._plate._grid._markers                  # no gear any more
    assert d2._plate._grid._fractions                    # a dial instead
    d2.set_well_stage("A01", "error")
    assert d2._plate._grid._markers                      # ✕ survives


def test_uploaded_and_computing_are_different_colours(qapp):
    """They used to share ``warn``, so a plate mid-run was one block of amber.

    Now that a computing well also carries a dial, the two differed in shape
    while their colour still claimed they were the same state.
    """
    import theme
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=2)
    d.set_well_stage("A01", "upload")
    d.set_well_stage("A02", "upload")
    d.set_well_stage("A02", "active")
    assert d._well_color("A01") == theme.Colors.accent2      # on the remote
    assert d._well_color("A02") == theme.Colors.warn         # being computed
    assert d._well_color("A01") != d._well_color("A02")


def test_an_upload_fills_the_dial_and_leaves_it_full(qapp):
    """Per-well upload used to be a single event — the archive landed or it did
    not — so an uploaded well carried no dial and a plate mid-run was a solid
    block of wells saying nothing.

    Upload does have a fraction (rsync's own byte stream, attributed per
    archive). It fills the dial as the bytes go, and the dial *stays* full when
    the transfer ends: the archive is on the remote in full, and what the well
    waits for next is its turn to compute, not more transfer. Emptying it here
    made the transfer appear to undo itself the moment it finished.
    """
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    d.set_well_transfer("A01", "up", 0.6)
    assert d._plate._grid._fractions[(0, 0)] == pytest.approx(0.6)
    d.set_well_stage("A01", "upload")
    d.set_well_transfer("A01", "up", 1.0)
    assert d._plate._grid._fractions[(0, 0)] == 1.0
    # …and only the start of compute takes it back to empty.
    d.set_well_stage("A01", "active")
    assert d._plate._grid._fractions[(0, 0)] == 0.0


def test_the_legend_names_uploaded_on_remote_runs_only(qapp):
    from PySide6.QtWidgets import QLabel

    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    remote_terms = {lbl.text() for lbl in d._legend_host.findChildren(QLabel)}
    assert "uploaded" in remote_terms
    d.reset(remote=False, total=1)
    local_terms = {lbl.text() for lbl in d._legend_host.findChildren(QLabel)}
    assert "uploaded" not in local_terms, "a local run never uploads"


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
    from theme import Colors
    assert d._well_color("A01") == Colors.accent      # blue: computed, awaiting fetch
    d.set_well_stage("A01", "download")
    assert d._count("download") == 1
    assert d._well_color("A01") == Colors.success  # green only once data is local


def test_dashboard_error_wins(qapp):
    from widgets.run_dashboard import RunDashboard
    from theme import Colors
    d = RunDashboard(); d.reset(remote=True, total=1)
    d.set_well_stage("A01", "process")
    d.set_well_stage("A01", "error")
    assert d._well_color("A01") == Colors.danger and "failed" in d._well_tooltip("A01")


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


def test_tracker_emits_per_well_transfer_progress():
    """Upload and download report a fraction per well, not just plate-wide."""
    from services.remote_runner import _well_xfer_line

    t = ProgressTracker()
    assert t.parse(_well_xfer_line("C02", "down", 0.418)) == [
        ("well_xfer", ("C02", "down", 0.418))]
    assert t.parse(_well_xfer_line("d4", "up", 0.5)) == [
        ("well_xfer", ("D4", "up", 0.5))]
    # A sample that cannot be read is dropped, not guessed at — the only thing
    # it drives is a gauge position.
    from services.remote_runner import WELL_XFER_PREFIX
    assert t.parse(f"{WELL_XFER_PREFIX}sideways C02 0.5") == []


def test_tracker_cached_upload_marks_well_uploaded():
    # A full cache hit logs "↑ <well>.zip already on remote" (no live transfer
    # lines) — it must still advance the Upload rail.
    t = ProgressTracker()
    ups = [p for k, p in t.parse("[remote] ↑ A01.zip already on remote") if k == "well_stage"]
    assert ("A01", "upload") in ups


def test_tracker_emits_finalize_status():
    t = ProgressTracker()
    assert ("status", "Auto-thresholding…") in t.parse(
        "Auto-threshold: scanning 3 well(s) for channels: GFP")
    assert ("status", "Auto-thresholding 2/3…") in t.parse(
        "Auto-threshold: completed well 2/3 (A02_out.zip)")
    assert ("status", "Finalizing downloads…") in t.parse(
        "[remote] ↓ fetcher: 5/7 wells downloaded so far")
    assert ("status", "Cleaning up remote…") in t.parse(
        "[remote] removed run dir abc123.")


def test_dashboard_fov_sub_progress(qapp):
    """Rewritten: FOV progress used to be drawn as a stack of labelled
    ``QProgressBar`` rows beneath the plate, one per computing well. The dial
    on the well itself carries the same information in the place the eye is
    already looking, so the rail is gone and this asserts the dial instead.
    """
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard(); d.reset(remote=True, total=2)
    d.set_well_stage("A01", "active"); d.set_well_stage("B02", "active")
    d.set_well_fov("A01", 0, 12); d.set_well_fov("B02", 0, 8)
    d.set_well_fov("A01", 7, 12)
    fractions = d._plate._grid._fractions
    assert fractions[(0, 0)] == pytest.approx(7 / 12)
    assert fractions[(1, 1)] == 0.0
    # a well that finishes processing reads full, not stuck at its last count
    d.set_well_stage("A01", "process")
    assert d._plate._grid._fractions[(0, 0)] == 1.0


def test_no_per_well_progress_bars_beneath_the_plate(qapp):
    """The rail is gone, and nothing may quietly rebuild it.

    It was a height-capped scroll area holding a ``QProgressBar`` per active
    well. Many concurrent wells could not grow the dashboard and crush the
    log, which was the whole reason for the cap — a constraint that stops
    existing once the information lives on the plate.
    """
    from PySide6.QtWidgets import QProgressBar

    from widgets.run_dashboard import RunDashboard
    d = RunDashboard(); d.reset(remote=False, total=8)
    for i, w in enumerate(["A01", "B02", "C03", "D04", "E05", "F06", "G07", "H08"]):
        d.set_well_stage(w, "active"); d.set_well_fov(w, i, 12)
    # The only bars left are the overall-completion bar and the stage rail's.
    bars = d.findChildren(QProgressBar)
    assert len(bars) == 1 + len(d._rail), [b.format() for b in bars]
    # And every well still says how far along it is, on the plate.
    assert len(d._plate._grid._fractions) == 8


def test_the_counts_behind_a_dial_are_in_its_tooltip(qapp):
    """A proportion cannot be counted off a circle, and the per-well progress
    bars that used to carry the numbers are gone — so the hover text does."""
    from widgets.run_dashboard import RunDashboard
    d = RunDashboard(); d.reset(remote=True, total=2)
    d.set_well_stage("A01", "active"); d.set_well_fov("A01", 7, 12)
    assert "FOV 7/12" in d._well_tooltip("A01")
    d.set_well_transfer("B02", "down", 0.42)
    assert "downloading 42%" in d._well_tooltip("B02")
    d.set_well_transfer("C03", "up", 0.10)
    assert "uploading 10%" in d._well_tooltip("C03")
    # A failed well says only that: a percentage beside it would suggest it is
    # still going, exactly as a part-filled dial would.
    d.set_well_stage("A01", "error")
    assert d._well_tooltip("A01") == "A01: failed"


def test_a_transfer_is_painted_in_the_colour_it_is_heading_toward(qtbot):
    """A well waiting on the server is blue; once its download starts it should
    fill GREEN, not blue.

    The dial fills from a light tint of its colour to the full one, so painting
    a download in the "on server" blue it is leaving made the well fill
    blue-into-blue — light meaning "not yet", on a plate where light is exactly
    how an unstarted dial looks. It also left no moment at which the well
    simply *was* blue: dark blue, then pale blue, then green. The colour change
    should be the arrival.
    """
    import theme
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    qtbot.addWidget(dash)
    dash.reset(remote=True, total=4)

    dash.set_well_stage("A01", "process")
    assert dash._well_color("A01") == theme.Colors.accent, "computed, awaiting fetch"

    # Download in flight — the well is becoming local, so it reads as local.
    dash.set_well_transfer("A01", "down", 0.4)
    assert dash._well_color("A01") == theme.Colors.success
    assert dash._well_fraction("A01") == pytest.approx(0.4)

    # And it stays that colour once the fetch lands.
    dash.set_well_stage("A01", "download")
    assert dash._well_color("A01") == theme.Colors.success


def test_an_upload_in_flight_reads_as_heading_to_the_remote(qtbot):
    """Same rule the other way: a well being uploaded is becoming "over there",
    so it takes the colour of a well that has landed there rather than the
    grouped-and-queued grey it is leaving."""
    import theme
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    qtbot.addWidget(dash)
    dash.reset(remote=True, total=4)

    dash.set_well_stage("B02", "group")
    assert dash._well_color("B02") == theme.Colors.text_muted

    dash.set_well_transfer("B02", "up", 0.5)
    assert dash._well_color("B02") == theme.Colors.accent2

    # A well that is already computing is not "heading to the remote" any more,
    # so a stale upload marker must not repaint it away from amber.
    dash.set_well_stage("B02", "active")
    assert dash._well_color("B02") == theme.Colors.warn


def test_local_auto_threshold_records_reach_the_line_stream():
    """The post-fetch auto-threshold pass runs in-process and reports itself
    through ``logging``. The GUI drives its Threshold stage by parsing the run's
    LINE stream, so those records were invisible to it — the only things on the
    stream were the two bookend lines the method logs itself, leaving a full
    plate's worth of silence at the end of a run.

    The forwarder must pass the pipeline's own message text through unchanged:
    the consumer regexes match that wording, so a prefix or timestamp here
    would break them exactly as a missing progress flag does."""
    import logging

    from services.remote_runner import _LogToLineStream

    lines = []
    log = logging.getLogger("allwell.test_auto_threshold_forward")
    log.handlers = []
    log.setLevel(logging.INFO)
    log.propagate = False
    handler = _LogToLineStream(lines.append)
    log.addHandler(handler)
    try:
        log.info("Auto-threshold: scanning %d well(s) for channels: %s", 40, "gfp")
        log.info("Auto-threshold: completed well %d/%d (%s)", 7, 40, "C08")
    finally:
        log.removeHandler(handler)

    assert lines == [
        "Auto-threshold: scanning 40 well(s) for channels: gfp\n",
        "Auto-threshold: completed well 7/40 (C08)\n",
    ]

    # And the shipped consumers actually match what came through.
    from services.pipeline_runner import _RE_TF_PROG, _RE_TF_SCAN, _RE_THRESH_WELL

    assert _RE_TF_SCAN.search(lines[0]).group(1) == "40"
    m = _RE_TF_PROG.search(lines[1])
    assert (m.group(1), m.group(2)) == ("7", "40")
    assert _RE_THRESH_WELL.search(lines[1]).group(1) == "C08"


# ── a transfer repaints the well, not just its dial ──────────────────────────
#
# Reported as: a well "started blue, then a pie that filled to blue, then stayed
# at blue despite the top progress bar stating that wells were actively
# downloading". ``set_well_stage`` pushed both the colour and the fraction to the
# plate; ``set_well_transfer`` pushed only the fraction. So the green
# ``_well_color`` computes for a download was thrown away on every tick and the
# well kept the blue of its last stage change — the arrival, the one thing the
# colour exists to show, never reached the screen.
#
# These assert on what the PLATE was told, not on what the dashboard computes.
# Asserting the computed value is what hid this: ``_well_color`` returned green
# throughout while the widget painted blue.

def _colour_spy(d):
    """Record every colour actually pushed to the plate."""
    pushed = []
    real = d._plate.setWellColor

    def _spy(well, colour):
        pushed.append(colour)
        return real(well, colour)

    d._plate.setWellColor = _spy
    return pushed


def test_a_download_repaints_the_well_green_not_just_the_dial(qapp):
    import theme
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    d.set_well_stage("A01", "process")            # computed: blue
    pushed = _colour_spy(d)

    d.set_well_transfer("A01", "down", 0.45)
    assert pushed, "a transfer must repaint the well, not only fill its dial"
    assert pushed[-1] == theme.Colors.success
    assert d._plate._grid._fractions[(0, 0)] == pytest.approx(0.45)


def test_an_upload_repaints_the_well_too(qapp):
    import theme
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    pushed = _colour_spy(d)
    d.set_well_transfer("A01", "up", 0.3)
    assert pushed[-1] == theme.Colors.accent2


def test_a_finished_zip_freezes_the_ring_instead_of_reverting_to_blue(qapp):
    """A well's output arrives as TWO transfers — the zip, then its CSVs — and
    only the zip has a measurable size. Ending the transfer at the zip returned
    the well to a full ring in the computed blue for the whole CSV pull, which
    is exactly what a well that had not started downloading looks like.

    The ring freezes at the last measured fraction instead: green, and visibly
    short of full, so "still fetching" cannot be read as either "not started" or
    "finished".
    """
    import theme
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    d.set_well_stage("A01", "process")
    d.set_well_transfer("A01", "down", 0.97)
    d.set_well_transfer("A01", "down", 1.0)       # the heartbeat closing its window

    assert d._well_color("A01") == theme.Colors.success
    assert d._well_fraction("A01") == pytest.approx(0.97), "the ring must not fill"

    d.set_well_stage("A01", "download")           # CSVs landed: now it is fetched
    assert d._well_color("A01") == theme.Colors.success
    assert d._well_fraction("A01") == 1.0
    assert "A01" not in d._xfer, "the frozen ring should be dropped once fetched"


def test_a_closing_download_with_no_prior_tick_invents_nothing(qapp):
    """A well whose remote size probe failed never had a dial. The heartbeat
    still closes its window on the way out, and that lone 1.0 must not conjure a
    full ring for a transfer whose progress was never measured."""
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    d.set_well_stage("A01", "process")
    d.set_well_transfer("A01", "down", 1.0)
    assert "A01" not in d._xfer


def test_a_finished_upload_still_ends_its_transfer(qapp):
    """Only a *download* holds its entry past 1.0; an upload still drops one.

    The asymmetry is the point. A download's 1.0 marks the end of the zip, with
    the CSVs still to come, so the ring has to keep describing a fetch that is
    not over. An upload's 1.0 is the whole transfer, so the entry goes and the
    stages take the dial back.

    What the dial then *reads* is not this test's business — an uploaded well
    holds a full dial so the fill never rewinds at the upload → compute
    handover, which ``test_a_finished_upload_hands_the_dial_back_to_the_stages``
    covers. This asserts only that the transfer itself ended.
    """
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    d.set_well_stage("A01", "upload")
    d.set_well_transfer("A01", "up", 0.5)
    assert d._xfer["A01"] == ("up", 0.5)
    d.set_well_transfer("A01", "up", 1.0)
    assert "A01" not in d._xfer, "an upload's 1.0 ends it"

    # ...where a download's does not.
    d.set_well_stage("B02", "process")
    d.set_well_transfer("B02", "down", 0.5)
    d.set_well_transfer("B02", "down", 1.0)
    assert d._xfer["B02"] == ("down", 0.5), "the zip is done; the CSVs are not"
