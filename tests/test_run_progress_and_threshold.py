"""Run-progress fixes (grouping progress when already grouped, well 'pending'
status) and the integer-rounding of the calculated auto-threshold.

Covers three reported issues:
  1) Grouping never showed progress when the inputs were already grouped/zipped
     — the zipper is skipped, so per-well group progress must be recovered from
     the resolved input dir (``iter_well_tokens``).
  2) Wells jumped straight from empty to processing — a grouped/queued well must
     render in a distinct 'pending' colour, separate from the 'processing' amber.
  3) The calculated auto-threshold must be rounded to the nearest integer before
     it is saved.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest


# ── 1) iter_well_tokens: recover wells when grouping is skipped ───────────────

def test_iter_well_tokens_from_zips_and_folders(tmp_path):
    from services.input_resolution_service import iter_well_tokens

    # per-well zips + a per-well folder + a non-well zip (ignored)
    (tmp_path / "H12.zip").write_bytes(b"")
    (tmp_path / "A1.zip").write_bytes(b"")          # short form → canonicalises to A01
    (tmp_path / "B02").mkdir()
    (tmp_path / "archive.zip").write_bytes(b"")     # not a well — skipped
    (tmp_path / "notes").mkdir()                    # not a well — skipped

    toks = iter_well_tokens(tmp_path)
    assert toks == ["A01", "B02", "H12"]            # canonical + sorted + deduped
    assert iter_well_tokens(tmp_path / "does_not_exist") == []


# ── 2) RunDashboard: grouped == pending (distinct from processing) ────────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_grouped_well_is_pending_not_processing(qapp):
    from widgets.run_dashboard import RunDashboard
    from theme import Colors
    d = RunDashboard()
    d.reset(remote=False, total=3)

    # a grouped (queued) well shows the pending colour, NOT the processing amber
    d.set_well_stage("A01", "group")
    assert d._well_color("A01") == Colors.text_muted
    assert "pending" in d._well_tooltip("A01") or "grouped" in d._well_tooltip("A01")

    # once it starts processing it flips to amber, then blue when processed
    d.set_well_stage("A01", "active")
    assert d._well_color("A01") == Colors.warn
    d.set_well_stage("A01", "process")
    assert d._well_color("A01") == Colors.accent

    # an untouched well stays uncoloured (truly empty, distinct from pending)
    assert d._well_color("H12") is None


def test_grouped_well_is_pending_remote(qapp):
    from widgets.run_dashboard import RunDashboard
    from theme import Colors
    d = RunDashboard()
    d.reset(remote=True, total=2)
    d.set_well_stage("A01", "group")
    assert d._well_color("A01") == Colors.text_muted   # queued, not yet uploaded
    d.set_well_stage("A01", "upload")
    # On the remote, waiting its turn — its own colour. This used to be ``warn``,
    # shared with a well actually being computed, so a plate mid-run was one
    # block of amber that said nothing about which wells were being worked on.
    assert d._well_color("A01") == Colors.accent2
    d.set_well_stage("A01", "active")
    assert d._well_color("A01") == Colors.warn          # now actually computing


# ── 3) Auto-threshold rounds to the nearest integer before saving ────────────

def _write_tif_bytes(arr: np.ndarray) -> bytes:
    import tifffile
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr.astype(np.float32))
    return buf.getvalue()


def _make_out_zip(path: Path) -> None:
    """A minimal ``*_out.zip`` with one FOV: a 2-label mask + its tophat fluor,
    so the standalone estimator pools a non-trivial cell+bg distribution."""
    schema_stem = "exp_GFP_A01_F001_T01"
    labels = np.zeros((16, 16), np.int32)
    labels[2:6, 2:6] = 1
    labels[10:14, 10:14] = 2
    fluor = np.zeros((16, 16), np.float32)
    fluor[2:6, 2:6] = 137.0        # bright cell
    fluor[10:14, 10:14] = 11.0     # dim cell
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{schema_stem}_labels.tif", _write_tif_bytes(labels))
        zf.writestr(f"{schema_stem}_tophat.tif", _write_tif_bytes(fluor))


# ── 4) ProgressTracker: incremental markers reach a terminal well state ───────

def test_incremental_progress_markers():
    """Skipped (up-to-date), merged, and new wells must all advance to a
    terminal dashboard state — and the per-well bar must reach total/total —
    on both local and remote runs."""
    from services.pipeline_runner import ProgressTracker

    pt = ProgressTracker()
    pt.parse("Zip mode: 4 well(s) to process, 2 worker(s).")  # well_total = 4

    def kinds(line):
        return pt.parse(line)

    # merged well: starting → FOV sub-progress → done.
    assert ("well_stage", ("A01", "active")) in kinds("Well A01 — starting (pid 9)")
    kinds("Well A01 — merging 2 new FOV(s).")
    assert ("well_fov", ("A01", 0, 2)) in kinds("Well A01 — 2 FOV-group(s)")
    ev = kinds("Well A01 — done.")
    assert ("well_done", (1, 4)) in ev
    assert ("well_stage", ("A01", "process")) in ev

    # up-to-date well: no "done." line, but its own marker counts + advances it.
    ev = kinds("Well B02 — up to date (no new FOVs).")
    assert ("well_done", (2, 4)) in ev
    assert ("well_stage", ("B02", "process")) in ev

    # new well: ordinary lifecycle.
    kinds("Well C03 — starting (pid 9)")
    assert ("well_done", (3, 4)) in kinds("Well C03 — done.")

    # remote up-to-date marker turns a never-fetched well green (download stage).
    assert ("well_stage", ("D04", "download")) in kinds("[remote] ✓ D04: up to date (local)")


def test_per_well_markers_tolerate_mangled_separator():
    """Remote (UTF-8) per-well markers decoded with a cp1252 Windows locale
    arrive with the em-dash mojibake'd. The Process stage must still track —
    previously it stuck at 0/N while ASCII-only threshold markers reached N/N."""
    from services.pipeline_runner import ProgressTracker

    for sep in ("—", "â€”", "�", "-"):  # em-dash, mojibake, replacement, hyphen
        pt = ProgressTracker()
        pt.parse("Zip mode: 20 well zip(s) detected.")
        ev = pt.parse(f"Well A01 {sep} starting (pid 1)")
        assert ("well_stage", ("A01", "active")) in ev, sep
        ev = pt.parse(f"Well A01 {sep} 3 FOV-group(s)")
        assert any(k == "well_fov" for k, _ in ev), sep
        ev = pt.parse(f"Well A01 {sep} done.")
        assert ("well_done", (1, 20)) in ev, sep
        assert ("well_stage", ("A01", "process")) in ev, sep


def test_standalone_auto_threshold_is_rounded_to_integer(tmp_path):
    pytest.importorskip("skimage")
    import logging
    from pipeline.auto_threshold import estimate_thresholds_from_outputs

    _make_out_zip(tmp_path / "A01_out.zip")
    thr = estimate_thresholds_from_outputs(
        tmp_path,
        fluor_channels=["GFP"],
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_",
        log=logging.getLogger("test"),
        rng_seed=0,
    )
    assert "gfp" in thr
    v = thr["gfp"]
    # the saved value is an integer-valued float (rounded, no fractional part)
    assert v == float(round(v))
    assert v == int(v)


# ── the status line belongs to whoever is current ───────────────────────────

def _progress_host(qapp):
    """The AnalyzeProgressMixin over a real RunDashboard.

    ``AnalyzeProgressMixin`` is a mixin whose host supplies ``_dashboard`` and
    ``_log_line``; the real host is ``AnalyzeTab``. It must be a QObject —
    ``_update_eta`` parents a QTimer to it.
    """
    from PySide6.QtWidgets import QWidget

    from analyze_progress import AnalyzeProgressMixin
    from widgets.run_dashboard import RunDashboard

    class _Host(AnalyzeProgressMixin, QWidget):
        def __init__(self):
            super().__init__()
            self._dashboard = RunDashboard()
            self._dashboard.reset(remote=True, total=2)
            self._well_total = 2
            self._well_done = 0
            self._well_repeats = 0
            self._pipeline_started_at = None
            self._zipper_done = 0

        def _log_line(self, *_a, **_k):
            return None

    return _Host()


@pytest.mark.parametrize("event,payload", [
    ("well_fov", ("A01", 1, 12)),
    ("well_total", 2),
    ("well_done", (1, 2)),
])
def test_a_compute_event_ends_the_transfer_label(qapp, event, payload):
    """The reported bug: after the upload finished and processing began, the
    status line kept reading ``Transferring… 92%`` for the rest of the run.

    Nothing in the compute phase touched the label — ``remote_xfer`` wrote it
    and only the end of the run cleared it, so it held whatever the last rsync
    tick happened to be. Keyed off the pipeline's own progress rather than off
    rsync reaching exactly 100%, which it need not do.
    """
    host = _progress_host(qapp)
    d = host._dashboard

    d.set_transfer(0.92)
    assert d._status.text() == "Transferring… 92%"

    host._apply_progress_event(event, payload)
    assert d._status.text() == ""
    assert d.transfer() is None


def test_finalizing_survives_the_transfer_clear(qapp):
    """``Finalizing…`` is set by the same branch that clears the transfer, and
    after it — the auto-threshold pass really is what is happening, so it must
    not be swallowed on the way past."""
    host = _progress_host(qapp)
    d = host._dashboard
    d.set_transfer(0.92)
    host._apply_progress_event("well_done", (2, 2))
    assert d._status.text() == "Finalizing…"


def test_a_reset_forgets_the_transfer(qapp):
    """A second run must not start wearing the first run's percentage."""
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(remote=True, total=1)
    d.set_transfer(0.5)
    d.reset(remote=True, total=1)
    assert d.transfer() is None
    assert d._status.text() == ""


def test_clearing_an_absent_transfer_leaves_other_status_text_alone(qapp):
    """A compute event arrives on local runs too, where no transfer ever ran.
    Clearing must be a no-op there rather than wiping whatever the status is
    legitimately showing."""
    from widgets.run_dashboard import RunDashboard

    d = RunDashboard()
    d.reset(total=1)
    d.set_status("Running…")
    d.set_transfer(None)
    assert d._status.text() == "Running…"


# ── a scheduler wait counts up instead of reading "Transferring… 100%" ───────

def test_the_wait_replaces_the_stale_transfer_label(qapp):
    """The reported symptom. ``set_transfer(None)`` fires only on a *compute*
    event, and during a queue wait no compute event has happened — so the last
    thing written stayed on screen while the run sat in SLURM's queue."""
    host = _progress_host(qapp)
    host._dashboard.set_transfer(1.0)
    assert host._dashboard.transfer() == 1.0

    host._apply_progress_event("sched", ("waiting", "", "srun -p gpu"))
    assert host._dashboard.transfer() is None
    assert "Waiting for the scheduler" in host._dashboard._status.text()


def test_the_wait_names_the_job_once_the_scheduler_does(qapp):
    host = _progress_host(qapp)
    host._apply_progress_event("sched", ("waiting", "", "srun -p gpu"))
    assert "job" not in host._dashboard._status.text(), "no id exists yet"

    host._apply_progress_event("sched", ("queued", "4812345", ""))
    assert "4812345" in host._dashboard._status.text()


def test_the_wait_counts_up(qapp):
    import time as _time

    host = _progress_host(qapp)
    host._apply_progress_event("sched", ("queued", "4812345", ""))
    host._sched_since = _time.monotonic() - 245
    host._render_scheduler()
    assert "4m05s" in host._dashboard._status.text()


def test_a_long_wait_repeats_itself_in_the_log(qapp):
    """With the request in it: an unsatisfiable one is the usual reason a job
    queues indefinitely, and it is not otherwise visible from the app."""
    logged: list[str] = []
    host = _progress_host(qapp)
    host._log_line = lambda text, level="INFO": logged.append(text)

    host._apply_progress_event("sched", ("waiting", "", "srun -p nonesuch"))
    host._apply_progress_event("sched", ("queued", "4812345", ""))
    host._sched_since -= 300
    host._sched_breadcrumb_at -= 300
    host._render_scheduler()

    assert logged, "a long wait must leave a trace in the log"
    assert "still waiting for the scheduler" in logged[-1]
    assert "4812345" in logged[-1] and "srun -p nonesuch" in logged[-1]


def test_being_granted_stops_the_clock(qapp):
    host = _progress_host(qapp)
    host._apply_progress_event("sched", ("queued", "4812345", ""))
    host._apply_progress_event("sched", ("granted", "4812345", ""))
    assert host._sched_since is None
    assert "Allocation granted" in host._dashboard._status.text()


def test_computing_stops_the_clock_and_retracts_the_message(qapp):
    """The backstop, for a launcher that announces nothing on the way out of
    the queue: the pipeline is demonstrably running, so the wait is over."""
    host = _progress_host(qapp)
    host._apply_progress_event("sched", ("queued", "4812345", ""))
    assert host._sched_since is not None

    host._apply_progress_event("well_fov", ("A01", 1, 4))
    assert host._sched_since is None
    assert host._dashboard._status.text() == ""


def test_the_status_line_is_only_retracted_when_it_is_ours(qapp):
    """Several writers share this line. A scheduler event must not clear one
    that somebody else put there."""
    host = _progress_host(qapp)
    host._dashboard.set_status("Finalizing…")
    host._apply_progress_event("well_done", (2, 2))
    assert host._dashboard._status.text() == "Finalizing…"


# ── a well this run did not compute is recorded as one it did not compute ────

def test_an_uncomputed_well_is_reported_as_such_without_changing_the_plate():
    """Three markers mean "complete"; only one means "this run computed it".

    ``— done.``, ``up to date`` and ``SKIP … output already exists`` all reach
    ``_count_well_done`` and all three must advance the plate, which is why they
    share a counter. Collapsing them for the *journal* too made an incremental
    run that touched 6 of 96 wells record 96 ``analysed``, and the readout then
    said "It recorded 96 finished" — a claim about work that did not happen.

    The plate still hears ``process`` for all three: it paints finished wells
    alike, and ``RunDashboard.set_well_stage`` ignores any stage outside its own
    roster, so a new value there would have stopped those wells being marked
    done — trading a wrong record for a wrong picture.
    """
    from services.pipeline_runner import ProgressTracker

    pt = ProgressTracker()
    pt.parse("Zip mode: 3 well(s) to process, 2 worker(s).")

    computed = pt.parse("Well A01 — done.")
    assert ("well_stage", ("A01", "process")) in computed
    assert not [e for e in computed if e[0] == "well_uncomputed"], (
        "a well the pipeline actually processed was reported as uncomputed")

    for line, well in (("Well B02 — up to date (no new FOVs).", "B02"),
                       ("SKIP well C03 — output already exists", "C03")):
        ev = pt.parse(line)
        assert ("well_stage", (well, "process")) in ev, (
            f"{well} stopped advancing the plate: {ev}")
        assert ("well_uncomputed", well) in ev, (
            f"{well} was recorded as analysed though this run did not compute "
            f"it: {ev}")


def test_the_journal_records_an_uncomputed_well_as_skipped(tmp_path, monkeypatch):
    """At the boundary: what reaches ``record_well``, not what the tracker returns."""
    import analyze_progress
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    token = J.open_run(out, input_dir="/in", mode="local", spec={})

    class _Runner:
        last_output_dir = out
        journal_token = token

    class _Mixin(analyze_progress.AnalyzeProgressMixin):
        def __init__(self):
            self._runner = _Runner()

    m = _Mixin()
    m._journal_well_stage("A01", "process")
    m._journal_well("B02", "skipped", reason="already up to date")

    rec = J.read_journal(out)
    assert rec.wells["A01"].state == "analysed"
    assert rec.wells["B02"].state == "skipped"
    assert "up to date" in rec.wells["B02"].reason
    tally = rec.tally()
    assert "1 finished" in tally and "1 skipped" in tally, tally
