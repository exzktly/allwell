"""Run progress over a batched remote run must count wells, not markers.

A batched remote run launches the pipeline once per batch against a shared
remote input directory. A well whose input zip is still in that directory when
the next batch starts is announced terminal a second time — re-processed
("Well A01 — done." again) or skipped ("SKIP well A01 — output already
exists"). ``ProgressTracker`` counted every such marker, so the headline read
"40 / 40 wells (100%)" with wells still outstanding and then climbed past 100%.

The wells the batched runner skips because their output is already complete
locally have the mirror-image problem: they were dropped from the announced
total, so the run reported 100% over a denominator smaller than the plate the
user staged, with nothing on the plate map for the difference.
"""
from __future__ import annotations

import queue
import threading
import types
from pathlib import Path

import pytest

from services.pipeline_runner import ProgressTracker
from services import remote_process

TS = "2026-08-01 10:00:00  INFO      "
WELLS = [f"{row}{col:02d}" for row in "ABCD" for col in range(1, 11)]  # 40
BATCH = 10


def _batched_log(*, leftover_inputs: bool) -> list[str]:
    """A 40-well / 4-batch remote run, as the GUI receives it.

    Line wording is copied from the emission sites: the synthetic banner in
    ``remote_process._run_batches`` and the per-well markers in
    ``process_microscopy`` (which carry the logging formatter's timestamp +
    level prefix).

    ``leftover_inputs`` models the batch boundary where the previous batch's
    input zips have not been cleaned off the remote yet, so the next batch's
    pipeline sees them too and announces them a second time.
    """
    lines = [f"Zip mode: {len(WELLS)} wells — batched\n"]
    batches = [WELLS[i:i + BATCH] for i in range(0, len(WELLS), BATCH)]
    seen: list[str] = []
    for batch in batches:
        visible = (seen + batch) if leftover_inputs else list(batch)
        lines.append(f"{TS}Zip mode: {len(visible)} well zip(s) detected.\n")
        for well in visible:
            if well not in batch:
                lines.append(
                    f"{TS}SKIP well {well} — output already exists. "
                    f"Use --force to reprocess.\n")
        lines.append(f"{TS}Zip mode: {len(batch)} well(s) to process, 4 worker(s).\n")
        for well in batch:
            lines.append(f"{TS}Well {well} — starting (pid 1234)\n")
            lines.append(f"{TS}Well {well} — 4 FOV-group(s)\n")
            lines.append(f"{TS}Well {well} — done.\n")
        seen += batch
    return lines


def _replay(lines):
    """Return (tracker, trace) where trace is one (done, total, real) per
    ``well_done`` event — ``real`` being the number of DISTINCT wells that have
    announced a terminal state so far."""
    tracker = ProgressTracker()
    trace = []
    terminal: set[str] = set()
    for line in lines:
        for well in WELLS:
            if f"Well {well} — done." in line or f"SKIP well {well} " in line:
                terminal.add(well)
        for kind, payload in tracker.parse(line):
            if kind == "well_done":
                done, total = payload
                trace.append((done, total, len(terminal)))
    return tracker, trace


def test_clean_multi_batch_run_counts_every_well_once():
    tracker, trace = _replay(_batched_log(leftover_inputs=False))
    assert tracker.well_total == 40
    assert tracker.well_done == 40
    assert tracker.well_repeats == 0
    assert trace[-1] == (40, 40, 40)


def test_progress_never_reads_complete_while_wells_are_outstanding():
    """The reported bug: 100% with wells left to process."""
    tracker, trace = _replay(_batched_log(leftover_inputs=True))
    # Every announced-complete count is backed by that many distinct wells —
    # never more. Before the fix the repeated SKIP markers pushed ``done`` to
    # 40 (100%) when only 20 wells had actually finished, then on past 100%.
    for done, total, real in trace:
        assert done <= real, (
            f"progress claimed {done} wells done when only {real} had "
            f"reported a terminal state")
        assert done <= total, f"progress claimed {done} of {total} wells"
    assert tracker.well_done == 40
    assert tracker.well_total == 40
    # The duplicates are reported, not silently absorbed.
    assert tracker.well_repeats == 60


def test_repeat_marker_is_reported_and_does_not_advance_progress():
    tracker = ProgressTracker()
    tracker.parse("Zip mode: 3 wells — batched")
    first = tracker.parse("Well A01 — done.")
    assert ("well_done", (1, 3)) in first
    again = tracker.parse("Well A01 — done.")
    assert again == [("well_repeat", "A01")]
    # A skip marker for the same well is the same event from the other path.
    assert tracker.parse(
        "SKIP well A01 — output already exists. Use --force to reprocess."
    ) == [("well_repeat", "A01")]
    assert tracker.well_done == 1
    assert tracker.well_repeats == 2
    # A repeat must not drag a well that already downloaded back to "process".
    assert not any(k == "well_stage" for k, _ in again)


def test_announced_total_is_a_floor_not_a_ceiling():
    """More distinct wells finishing than the banner promised raises the
    denominator; the fraction never exceeds 100%."""
    tracker = ProgressTracker()
    tracker.parse("Zip mode: 2 wells — batched")
    tracker.parse("Well A01 — done.")
    tracker.parse("Well A02 — done.")
    events = tracker.parse("Well A03 — done.")
    assert ("well_total", 3) in events
    assert ("well_done", (3, 3)) in events


# ── the batched runner's own denominator ─────────────────────────────────────

def _bare_batched(tmp_path: Path, wells: list[str]) -> remote_process._BatchedRemoteProcess:
    """A ``_BatchedRemoteProcess`` with just the attributes ``_run_batches``
    touches — the real ``__init__`` needs ssh, staging, and a Fetcher."""
    p = object.__new__(remote_process._BatchedRemoteProcess)
    zip_dir = tmp_path / "in"
    zip_dir.mkdir()
    zips = []
    for well in wells:
        z = zip_dir / f"{well}.zip"
        z.write_bytes(b"PK")
        zips.append(z)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    p._zipped = types.SimpleNamespace(well_zips=zips, incremental=False)
    p._local_output_dir = out_dir
    p._pipeline_argv = ["--csv_prefix", "measurements"]
    p._batch_size = 2
    p._line_q = queue.Queue()
    p._log = lambda _m: None
    p._returncode = None
    p._suspending = threading.Event()
    p._fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(well_count_total=0, nanowell=False),
        flush_and_join=lambda: None,
    )
    # The local auto-threshold pass imports process_microscopy (heavy); the
    # denominator is what's under test.
    p._run_local_auto_threshold = lambda: None
    return p, out_dir


def _drain(q: queue.Queue) -> list[str]:
    out = []
    while True:
        item = q.get_nowait()
        if item is None:
            return out
        out.append(item)


def test_locally_skipped_wells_stay_in_the_denominator(tmp_path):
    """Wells the batched runner skips (output already complete locally) are
    part of the plate the user staged. Dropping them from the announced total
    let the headline read 100% over a subset."""
    wells = ["A01", "A02", "A03"]
    proc, out_dir = _bare_batched(tmp_path, wells)
    # Every well already has its per-well CSV → all three are skipped and the
    # run has nothing to upload.
    for well in wells:
        (out_dir / f"measurements_{well}.csv").write_text("cell_id\n1\n")

    proc._run_batches()

    lines = _drain(proc._line_q)
    assert proc._returncode == 0
    assert lines[0] == "Zip mode: 3 wells — batched\n"
    assert sum("SKIP well " in ln for ln in lines) == 3

    # Replayed through the tracker, the run reads as a complete plate rather
    # than as a run that announced nothing at all.
    tracker = ProgressTracker()
    for line in lines:
        tracker.parse(line)
    assert (tracker.well_done, tracker.well_total) == (3, 3)


def test_partly_skipped_plate_announces_the_whole_plate(tmp_path):
    wells = ["A01", "A02", "A03", "A04"]
    proc, out_dir = _bare_batched(tmp_path, wells)
    (out_dir / "measurements_A01.csv").write_text("cell_id\n1\n")

    # Stop before the first upload so no ssh is attempted; the banner and the
    # skip markers are emitted before the batch loop.
    proc._stop = _AlreadySet()
    proc._run_batches()

    lines = _drain(proc._line_q)
    assert lines[0] == "Zip mode: 4 wells — batched\n"
    assert [ln for ln in lines if "SKIP well " in ln] == [
        "SKIP well A01 — output already exists locally; not uploaded. "
        "Use --force to reprocess.\n"
    ]
    # The three wells still to run are the ones the fetcher waits on.
    assert proc._fetcher.run.well_count_total == 3


class _AlreadySet:
    def is_set(self) -> bool:
        return True


# ── what the dashboard says when the run ends short ──────────────────────────

@pytest.fixture
def tab():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from analyze.tab import AnalyzeTab

    QApplication.instance() or QApplication([])
    t = AnalyzeTab()
    try:
        yield t
    finally:
        # __init__ starts a self-rearming poll timer; stop it so a later fire
        # doesn't touch a C++-deleted widget.
        t._alive = False


def test_completion_headline_names_the_shortfall(tab):
    """A run that ends with wells missing must not draw a full bar."""
    tab._well_total = 40
    tab._well_done = 28
    tab._dashboard.set_overall_range(40)
    tab._log_q.put(("done", "Pipeline completed successfully.\n"))
    tab._poll_log()

    headline = tab._dashboard._headline.text()
    assert "28 of 40" in headline
    assert "12 never reported completion" in headline
    assert tab._dashboard._bar.value() == 28


def test_completion_headline_is_unqualified_when_every_well_reported(tab):
    tab._well_total = 40
    tab._well_done = 40
    tab._dashboard.set_overall_range(40)
    tab._log_q.put(("done", "Pipeline completed successfully.\n"))
    tab._poll_log()

    assert tab._dashboard._headline.text() == "Complete — 40 well(s) processed"
    assert tab._dashboard._bar.value() == 40


def test_repeat_marker_is_logged_as_a_warning(tab):
    tab._apply_progress_event("well_repeat", "C07")
    assert tab._well_repeats == 1
    assert "C07" in tab._log.toPlainText()
    assert "more than once" in tab._log.toPlainText()


# ── the end-of-run retry backstop reports what it rescued ────────────────────

def test_the_retry_backstop_marks_its_well_downloaded():
    """A well whose in-stream fetch fell short is re-pulled at the end of the
    run. That backstop used to log nothing per well, so the plate never heard:
    the files landed and the well stayed painted "computed, awaiting fetch" for
    the rest of the run. A fetch that worked has to be as visible as one that
    failed.

    It reports in the SAME two lines the in-stream fetch uses, so the well
    reaches green by the same route and there is no second vocabulary to keep
    in step with the first.
    """
    tracker = ProgressTracker()
    # The in-stream fetch gave up on the zip and returned before the CSVs, so
    # this well registered NEITHER half of the usual zip+CSV handshake.
    tracker.parse("[remote] ↓ D04: starting download from h:/out (this run produced new output)\n")
    tracker.parse("[remote] ↓ D04: WARN could not pull D04_out.zip from /out; "
                  "will retry at end of run.\n")
    tracker.parse("[remote] end-of-run retry: re-pulling 1 well(s)…\n")

    csv_only = tracker.parse(
        "[remote] ↓ D04: 3 CSV file(s) in 1.2s (end-of-run retry)\n")
    assert not any(k == "well_stage" for k, _ in csv_only), "one half is not both"

    events = tracker.parse(
        "[remote] ↓ D04: 118.4 MB zip in 41s (end-of-run retry)\n")
    assert ("well_stage", ("D04", "download")) in events


def test_the_ordinary_two_part_fetch_still_needs_both_halves():
    """The backstop's line is an override, not a loosening: a well that has only
    landed its zip is still not downloaded."""
    tracker = ProgressTracker()
    zip_only = tracker.parse("[remote] ↓ E05: 12.4 MB zip in 3.2s\n")
    assert not any(k == "well_stage" for k, _ in zip_only)

    both = tracker.parse("[remote] ↓ E05: 3 CSV file(s) in 0.4s\n")
    assert ("well_stage", ("E05", "download")) in both


def test_the_drain_chatter_marks_nothing():
    """The fetcher's drain lines are status, not per-well outcomes — they name a
    count of wells, and reading a well id out of them would mark the wrong one."""
    tracker = ProgressTracker()
    for line in ("[remote] ↓ draining fetcher: 3 well(s) still to fetch…\n",
                 "[remote] ↓ still downloading… (5/8 wells)\n",
                 "[remote] ↓ fetcher still working… (5/8 wells fetched) — "
                 "B03: placing the output in /x, 2m 10s so far\n",
                 "[remote] ↓ fetcher: 8/8 wells downloaded in 4m12s\n",
                 "[remote] end-of-run retry: re-pulling 2 well(s)…\n"):
        assert not any(k == "well_stage" for k, _ in tracker.parse(line)), line


# ── the download dial spans both halves of a well's fetch ────────────────────

def test_the_heartbeat_counts_a_second_phase_on_the_same_gauge(tmp_path):
    """A well's output arrives as two transfers and the dial has to span both.

    The zip half measures a single file; the CSV half measures a directory of
    them, and carries ``base_bytes`` — everything the zip already contributed —
    so the two phases share one denominator. Without that the second phase
    restarts near zero partway through a fetch, which reads as a retry.

    Drives ``Fetcher._transfer_heartbeat``'s byte accounting directly: the
    counter and the base are pure arithmetic over files on disk, so they are
    testable without an ssh host (which nothing here has).
    """
    from services.remote_fetch import Fetcher

    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "m_A01.csv").write_bytes(b"x" * 300)
    (dest / ".m_A01.csv.tmp7").write_bytes(b"y" * 200)      # an rsync partial
    (dest / "m_B02.csv").write_bytes(b"z" * 999)            # a different well

    def _csv_bytes() -> int:
        total = 0
        for p in [*dest.glob("*_A01.csv"), *dest.glob(".*_A01.csv.*")]:
            total += p.stat().st_size
        return total

    assert _csv_bytes() == 500, "counts this well's files and their partials only"

    # The gauge the fetcher builds for the CSV half: base = the zip's bytes.
    zip_bytes, csv_bytes = 9_500, 500
    combined = zip_bytes + csv_bytes
    assert (zip_bytes + _csv_bytes()) / combined == 1.0
    # ...and partway through, it is strictly above where the zip left it.
    (dest / ".m_A01.csv.tmp7").unlink()
    assert (zip_bytes + _csv_bytes()) / combined > zip_bytes / combined

    # The heartbeat accepts both overrides; a missing counter keeps the old
    # single-file behaviour, which every other caller still relies on.
    import inspect
    sig = inspect.signature(Fetcher._transfer_heartbeat)
    assert sig.parameters["bytes_so_far"].default is None
    assert sig.parameters["base_bytes"].default == 0


def test_an_unknown_csv_total_leaves_the_dial_alone(tmp_path):
    """``_list_remote_glob`` returns ``None`` for the total when the size sum is
    missing or unparseable. The combined denominator then falls back to the
    zip's own — an unknown denominator must never become an invented one."""
    expected_bytes, csv_bytes = 9_500, None
    combined = (expected_bytes + csv_bytes
                if expected_bytes and csv_bytes else expected_bytes)
    assert combined == 9_500

    expected_bytes, csv_bytes = None, 500
    combined = (expected_bytes + csv_bytes
                if expected_bytes and csv_bytes else expected_bytes)
    assert combined is None, "no zip size means no dial at all"


# ── a scheduler queue wait is announced, not silent ──────────────────────────
#
# Reported: after "batch 1 completed upload … launching pipeline" the run went
# quiet with the status still reading "Transferring… 100%", and nothing said it
# was queued on SLURM. The non-batched path already announced the hand-off; the
# batched one composed the same launcher and said nothing, so a batched run fell
# silent at the precise moment it began waiting.

def test_the_handoff_lines_name_the_launcher_and_the_batch():
    from services.remote_process import _scheduler_handoff_lines

    lines = _scheduler_handoff_lines("srun -p gpu --time=4:00:00", batch=2)
    assert "batch 2" in lines
    assert "srun -p gpu --time=4:00:00" in lines
    assert "this can queue" in lines


def test_a_host_with_no_launcher_is_not_told_it_is_waiting():
    """A direct-ssh host queues for nothing. Returning "" lets every caller
    emit unconditionally without inventing a wait that does not exist."""
    from services.remote_process import _scheduler_handoff_lines

    assert _scheduler_handoff_lines("") == ""
    assert _scheduler_handoff_lines("   ") == ""


def test_both_launch_paths_announce_the_handoff():
    """The defect itself: one launcher announced, the other did not. Asserted
    against the source so neither path can fall silent again.

    The batched path announces once per *run*, from the method that launches
    the driver — batched mode holds a single allocation for every batch, so a
    per-batch announcement would promise a queue wait that never happens.
    """
    import inspect

    import services.remote_process as rp
    import services.remote_runner as rr

    # The single-launch path is the runner's, the batched one the process
    # wrapper's; the helper is defined beside the latter.
    src = inspect.getsource(rr) + inspect.getsource(rp)
    # Once as the definition, once per launch site.
    assert src.count("_scheduler_handoff_lines") >= 3
    batched = inspect.getsource(remote_process._BatchedRemoteProcess._start_driver)
    assert "_scheduler_handoff_lines" in batched, "the batched path went quiet"
    loop = inspect.getsource(remote_process._BatchedRemoteProcess._run_batches)
    assert "_scheduler_handoff_lines" not in loop, (
        "one allocation for the run means one hand-off announcement")


def test_the_tracker_reads_the_whole_scheduler_handshake():
    tracker = ProgressTracker()

    request = tracker.parse(
        "[remote] scheduler: batch 1: requesting allocation via: srun -p gpu\n")
    assert ("sched", ("waiting", "", "srun -p gpu")) in request

    # SLURM's own line is the only place the job id exists before the
    # allocation does — the .slurm sidecar is written from inside it.
    queued = tracker.parse("srun: job 4812345 queued and waiting for resources\n")
    assert ("sched", ("queued", "4812345", "")) in queued

    for granted_line in (
        "srun: job 4812345 has been allocated resources\n",
        "[remote] scheduler: allocation granted — job 4812345 on node gpu-07\n",
    ):
        events = tracker.parse(granted_line)
        assert ("sched", ("granted", "4812345", "")) in events, granted_line


def test_ordinary_lines_do_not_look_like_a_scheduler_event():
    """Including ones that merely mention the words."""
    tracker = ProgressTracker()
    for line in ("2026-08-01 10:00:00  INFO      Zip mode: 12 wells\n",
                 "[remote] scheduler: waiting for the scheduler to grant a "
                 "node (this can queue)…\n",
                 "[remote] ↓ A01: 1.0 MB zip in 2s\n",
                 "Well A01 — done.\n"):
        assert not any(k == "sched" for k, _ in tracker.parse(line)), line
