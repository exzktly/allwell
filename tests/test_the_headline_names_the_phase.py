"""The run headline says what the run is doing *now*.

Reported: with the plate uploaded and the job sitting in a SLURM queue, the line
under **Run Progress** still read *Grouped — pipeline (segmentation + zipping)…*
— a sentence about a step that had finished, naming the one thing that was not
happening.

The cause was ownership rather than wording. Every phase that had something to
say wrote the headline itself, and the phases that had nothing to say — the
staging step, the upload, a scheduler queue wait, the post-well finalize — wrote
nothing and inherited whatever the last writer had left there. So the headline
was accurate only during grouping and during compute, and stale for everything
in between, which on a remote run is most of the wall clock.

The headline is now derived from the phase the run is in
(``AnalyzeProgressMixin._phase_headline``), and every one of these tests asserts
on the label the user actually reads — ``dashboard._headline.text()`` — not on
the phase state behind it. A phase that is set and never rendered is exactly the
failure this file exists to catch.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _host(qapp, *, remote: bool = True, total: int = 40):
    """The progress mixin over a real dashboard, as ``AnalyzeTab`` hosts it.

    A QWidget, because ``_update_eta`` parents a QTimer to its host.
    """
    from PySide6.QtWidgets import QWidget

    from analyze_progress import AnalyzeProgressMixin
    from widgets.run_dashboard import RunDashboard

    class _Host(AnalyzeProgressMixin, QWidget):
        def __init__(self):
            super().__init__()
            self._dashboard = RunDashboard()
            self._dashboard.reset(remote=remote, total=total)
            self._well_total = 0
            self._well_done = 0
            self._well_repeats = 0
            self._zipper_done = 0
            self._pipeline_started_at = None
            self.logged: list[str] = []

        def _log_line(self, text, level="INFO"):
            self.logged.append(text)

    host = _Host()
    # Guarded so this file can be run against pre-fix code to prove it bites:
    # the mixin there has no phase to reset, and an AttributeError raised in
    # the fixture would fail every test in the file without any of them ever
    # reaching an assertion about the headline — a proof that measured nothing
    # (CLAUDE.md §7.4 #4). Against the current tree the attribute is always
    # there, so this is not a code path the app can take.
    reset = getattr(host, "_reset_run_phase", None)
    if reset is not None:
        reset(remote=remote)
    return host


def _group_the_plate(host, wells) -> None:
    """Drive the grouping phase to completion, as a run does before anything else."""
    host._apply_grouping_event("zipper_start", len(wells))
    for well in wells:
        host._apply_grouping_event("zipper_well", well)
    host._apply_grouping_event("zipper_done", None)


def _headline(host) -> str:
    return host._dashboard._headline.text()


# ── the reported case ────────────────────────────────────────────────────────

def test_a_scheduler_wait_is_not_still_called_grouping(qapp):
    """The report, driven in the order the app performs it: group the plate,
    upload it, then sit in the queue."""
    host = _host(qapp)
    _group_the_plate(host, ["A01", "A02"])
    host._apply_progress_event("stage", "upload")
    host._apply_progress_event("well_stage", ("A01", "upload"))

    host._apply_progress_event("sched", ("waiting", "", "srun -p gpu --gres=gpu:1"))

    line = _headline(host)
    assert "Grouped" not in line, line
    assert "segmentation" not in line, line
    assert "scheduler" in line.lower(), line


def test_the_wait_headline_leaves_the_clock_to_the_status_line(qapp):
    """Two live clocks for one wait is how the two come to disagree; the status
    line is the one that ticks."""
    host = _host(qapp)
    host._apply_progress_event("sched", ("queued", "4812345", ""))

    assert "4812345" in host._dashboard._status.text()
    assert "4812345" not in _headline(host), _headline(host)


def test_being_granted_a_node_says_the_pipeline_is_starting(qapp):
    host = _host(qapp)
    host._apply_progress_event("sched", ("queued", "4812345", ""))
    host._apply_progress_event("sched", ("granted", "4812345", ""))
    assert "Starting the pipeline" in _headline(host), _headline(host)


# ── the phases either side of it ─────────────────────────────────────────────

def test_grouping_counts_the_wells_it_has_grouped(qapp):
    host = _host(qapp)
    host._apply_grouping_event("zipper_start", 4)
    assert _headline(host) == "Grouping 0 / 4 wells  (0%)"
    host._apply_grouping_event("zipper_well", "A01")
    assert _headline(host) == "Grouping 1 / 4 wells  (25%)"


def test_a_local_run_is_not_told_it_is_preparing_an_upload(qapp):
    host = _host(qapp, remote=False)
    _group_the_plate(host, ["A01"])
    line = _headline(host)
    assert "upload" not in line.lower(), line
    assert "pipeline" in line.lower(), line


def test_a_remote_run_names_the_upload_it_is_preparing(qapp):
    host = _host(qapp)
    _group_the_plate(host, ["A01"])
    assert "upload" in _headline(host).lower(), _headline(host)


def test_the_upload_counts_against_what_is_being_sent(qapp):
    """Not against the plate. An incremental remote run groups every well and
    uploads only the ones that need recomputing, and the run's own total does
    not arrive until the pipeline announces it — after the upload is over."""
    host = _host(qapp, total=40)
    host._apply_grouping_event("stage_total", ("upload", 6))
    host._apply_progress_event("stage", "upload")
    host._apply_progress_event("well_stage", ("A01", "upload"))
    host._apply_progress_event("well_stage", ("A02", "upload"))
    assert _headline(host) == "Uploading 2 / 6 wells  (33%)"


def test_processing_counts_wells(qapp):
    host = _host(qapp)
    host._apply_progress_event("well_total", 4)
    assert _headline(host) == "Processing 0 / 4 wells  (0%)"
    host._apply_progress_event("well_done", (1, 4))
    assert _headline(host) == "Processing 1 / 4 wells  (25%)"


def test_the_last_well_hands_the_headline_to_the_finalize_phase(qapp):
    """The auto-threshold pass, the download drain and the remote cleanup all
    run after the final well. "Processing 4 / 4 wells  (100%)" through them
    named the one thing the run had finished doing."""
    host = _host(qapp)
    host._apply_progress_event("well_total", 4)
    host._apply_progress_event("well_done", (4, 4))
    line = _headline(host)
    assert "finalizing" in line.lower(), line
    assert "Processed 4 well(s)" in line, line
    # ...and the detail of *what* is being finalized stays on the status line.
    assert host._dashboard._status.text() == "Finalizing…"


# ── the interleaving that a naive "last event wins" gets wrong ───────────────

def test_a_download_mid_run_does_not_rewind_the_headline(qapp):
    """The fetcher pulls a finished well's output while the wells after it are
    still computing, so ``("stage", "download")`` arrives interleaved with
    compute events. Letting it name the phase flipped the headline between
    "Downloading" and "Processing" for the length of a plate."""
    host = _host(qapp)
    host._apply_progress_event("well_total", 4)
    host._apply_progress_event("well_done", (1, 4))
    host._apply_progress_event("stage", "download")
    assert _headline(host) == "Processing 1 / 4 wells  (25%)"


def test_a_second_batch_uploading_is_not_reported_as_processing(qapp):
    """Batched remote runs really do go back to uploading: each batch is
    uploaded, computed and fetched before the next one is staged. The phases
    cycle, so the headline must follow rather than only ever move forwards."""
    host = _host(qapp)
    host._apply_grouping_event("stage_total", ("upload", 8))
    host._apply_progress_event("well_total", 4)
    host._apply_progress_event("well_done", (4, 4))
    host._apply_progress_event("stage", "upload")
    assert _headline(host).startswith("Uploading"), _headline(host)


def test_a_field_measured_ends_a_wait_that_was_never_announced(qapp):
    """The backstop for a host whose scheduler hand-off says nothing on the way
    out of the queue: a field measured is a well computing, whatever the run
    last announced."""
    host = _host(qapp)
    host._apply_progress_event("sched", ("queued", "4812345", ""))
    assert "scheduler" in _headline(host).lower()

    host._well_total = 4
    host._apply_progress_event("well_fov", ("A01", 1, 12))
    assert _headline(host) == "Processing 0 / 4 wells  (0%)"


# ── the roster ───────────────────────────────────────────────────────────────

def test_every_phase_says_something(qapp):
    """A phase with no sentence renders nothing and silently inherits the
    previous phase's — the exact defect this module was rewritten to remove —
    so a phase constant added without a branch must fail here."""
    import analyze_progress as ap

    phases = [v for k, v in vars(ap).items()
              if k.startswith("_PHASE_") and isinstance(v, str)]
    assert len(phases) >= 8, phases
    host = _host(qapp)
    for phase in phases:
        host._run_phase = phase
        assert host._phase_headline(), f"{phase} renders no headline"


def test_an_unknown_phase_leaves_the_label_alone(qapp):
    """The run's terminal states (Complete / Failed / Cancelled) are written to
    the dashboard by the tab. A phase this module cannot name must not blank
    them, so it renders nothing rather than an empty string."""
    host = _host(qapp)
    host._dashboard.set_headline("Complete — 40 well(s) processed")
    host._set_run_phase("a phase from the future")
    assert _headline(host) == "Complete — 40 well(s) processed"


# ── the run's terminal state outranks the phase machine ─────────────────────

def test_a_late_event_does_not_overwrite_a_failure(qapp):
    """A remote run keeps talking after it dies: ``verify_and_cleanup`` fetches
    what it can and logs it, so ``well_stage`` events arrive after the failure
    has been painted. Re-deriving the headline from the phase there would
    replace the reason the run failed with a sentence about a phase that is no
    longer running."""
    host = _host(qapp)
    host._apply_progress_event("well_total", 4)
    host._dashboard.set_failed("Not enough scratch space on the remote",
                               tooltip="the whole refusal")

    host._apply_progress_event("well_stage", ("A01", "download"))
    host._apply_progress_event("stage", "process")

    assert "scratch space" in _headline(host), _headline(host)


def test_a_late_event_does_not_overwrite_the_completion(qapp):
    """The success headline is not one of the dashboard's outcome states — the
    tab writes it as plain text so a shortfall is not painted green — so the
    end of the run is latched here instead."""
    host = _host(qapp)
    host._apply_progress_event("well_total", 4)
    host._end_run_phase()
    host._dashboard.set_headline("Complete — 4 well(s) processed")

    host._apply_progress_event("well_done", (4, 4))
    assert _headline(host) == "Complete — 4 well(s) processed"


def test_the_next_run_gets_its_headline_back(qapp):
    """The latch is per run. One that outlived its run would leave the next
    plate's dashboard frozen on the previous run's last words."""
    host = _host(qapp)
    host._end_run_phase()
    host._dashboard.set_headline("Complete — 4 well(s) processed")

    host._dashboard.reset(remote=True, total=4)
    host._reset_run_phase(remote=True)
    host._apply_grouping_event("zipper_start", 4)
    assert _headline(host) == "Grouping 0 / 4 wells  (0%)"
