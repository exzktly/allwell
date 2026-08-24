"""Suspending a run the Datasets Job Queue started.

Two things have to hold, and only one of them came for free.

**The queue must pause, not march on.** ``suspend()`` goes through ``stop()``, so
``was_cancelled()`` is true, so ``JobQueueController._on_job_finished`` marks the
job cancelled and pauses the queue. That is the behaviour the queue's own
decision table already specifies for a cancel, and it is the right one here: it
stops the next job starting underneath a user who has just asked for their
machine back, and it stops the resume banner offering to restart a plate while
the queue runs something else.

**Restarting that job from the queue must be incremental.** This did not come for
free. ``incremental`` is not a form field — it is set by
``input_resolution_service`` when loose images are appended, or by a resume — so
a queued job's stored spec never carries it. Re-running such a spec skips every
well whose output zip exists and processes the rest, which recovers the wells
that never began but silently abandons the ones that were *in flight*: those
wrote a CSV and an out.zip from the FOV-groups they had finished, so they read as
complete and are skipped, and their missing groups are never recovered. The plate
comes back looking done. ``resume_aware_spec`` closes that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import services.suspend_record as sr
from analyze_suspend import AnalyzeSuspendMixin
from services.run_spec import RunSpec


@pytest.fixture(autouse=True)
def _fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


class _Host(AnalyzeSuspendMixin):
    """Just the surface ``resume_aware_spec`` reads."""

    def __init__(self):
        self.logged: list[str] = []

    def _log_line(self, text, level="INFO"):
        self.logged.append(text)


def _spec(raw: str, **opts) -> RunSpec:
    return RunSpec(opts={"raw": raw, **opts}, remote_name=None)


def _record(input_dir: str, output_dir: str = "/data/out"):
    sr.save(sr.SuspendedRun(
        spec={"opts": {"raw": input_dir}}, input_dir=input_dir,
        output_dir=output_dir, suspended_at=1000.0, wells_done=4, wells_total=10,
    ))


def test_a_queued_job_with_no_suspended_run_is_untouched():
    host = _Host()
    spec = _spec("/data/in")
    assert host.resume_aware_spec(spec) is spec
    assert host.logged == []


def test_a_queued_job_whose_input_was_suspended_becomes_incremental():
    _record("/data/in")
    host = _Host()
    out = host.resume_aware_spec(_spec("/data/in"))
    assert out.opts["incremental"] is True
    assert out.opts["raw"] == "/data/in"
    assert any("incrementally" in line for line in host.logged), (
        "the user must be told why the run changed shape")


def test_a_different_plate_is_not_dragged_into_incremental():
    _record("/data/in")
    host = _Host()
    out = host.resume_aware_spec(_spec("/somewhere/else"))
    assert "incremental" not in out.opts
    assert host.logged == []


def test_an_explicitly_incremental_spec_is_left_alone():
    _record("/data/in")
    host = _Host()
    spec = _spec("/data/in", incremental=True)
    assert host.resume_aware_spec(spec) is spec


def test_the_rest_of_the_spec_survives_the_rewrite():
    _record("/data/in")
    host = _Host()
    spec = RunSpec(
        opts={"raw": "/data/in", "workers": 4},
        remote_name="cluster", cleanup_remote="all", dry_run_cleanup=True,
        output_filter="csv_only", selective_channels=False,
    )
    out = host.resume_aware_spec(spec)
    assert out.remote_name == "cluster"
    assert out.cleanup_remote == "all"
    assert out.dry_run_cleanup is True
    assert out.output_filter == "csv_only"
    assert out.selective_channels is False
    assert out.opts["workers"] == 4


def test_an_unreadable_record_never_blocks_a_queued_run(monkeypatch):
    """A queue job must start even if the resume bookkeeping is broken."""
    def _boom():
        raise OSError("record unreadable")

    monkeypatch.setattr("analyze_suspend.load_all", _boom)
    host = _Host()
    spec = _spec("/data/in")
    assert host.resume_aware_spec(spec) is spec


def test_start_from_queue_routes_through_it(monkeypatch):
    """The wiring, not just the helper: the queue's entry point must use it."""
    import inspect

    from analyze_run_spec import AnalyzeRunMixin

    src = inspect.getsource(AnalyzeRunMixin.start_from_queue)
    assert "resume_aware_spec" in src


def test_a_suspend_reads_as_cancelled_so_the_queue_pauses():
    """``was_cancelled`` is what ``_on_job_finished`` consults.

    True here means the queue marks the job cancelled and pauses rather than
    starting the next one underneath the user.
    """
    from services.pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    assert runner.was_stopped is False
    runner.suspend()
    assert runner.was_stopped is True     # what AnalyzeRunMixin.was_cancelled reads
    assert runner.was_suspended is True


# ── one owner per suspended run ─────────────────────────────────────────────

def test_the_banner_does_not_offer_a_run_the_queue_owns(tmp_path, monkeypatch):
    """Two surfaces offer an unfinished run; only one may offer any given one.

    Suspending a queue-driven job closes its journal ``suspended``, which is
    precisely what ``is_unfinished`` is for — so the Analyze banner offered the
    plate the moment ``_running`` cleared, while the queue still held the job.
    Resuming from the banner and then letting the queue start ran the same plate
    twice, concurrently, into one output directory.
    """
    import analyze_suspend
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()

    class _Pane(analyze_suspend.AnalyzeSuspendMixin):
        pass

    def _offers(queued: bool) -> list:
        J.open_run(out, input_dir="/in", mode="local",
                   spec={"opts": {"raw": "/in"}}, queued=queued)
        J.close_run(out, outcome="suspended")
        return _Pane()._journal_offers(only_dir=str(out))

    assert _offers(queued=False), (
        "the fixture no longer produces an offer at all, so the assertion "
        "below would pass for the wrong reason")
    assert _offers(queued=True) == [], (
        "the banner offered a run the job queue is already holding; resuming "
        "here runs the plate a second time into the same output folder")


def test_the_banner_asks_both_records_and_filters_both(tmp_path, monkeypatch):
    """`_journal_offers` was filtered and `_suspended_offers` was not.

    A suspend writes *both* pointers — `_suspend` calls `_write_suspend_record`
    for any running job — and `_resumable` ranks the suspend record **first**
    for the current folder, so the unfiltered one won and the plate was offered
    anyway. Filtering one of two records is filtering neither, which is the
    half-sweep §7.5 #5 is about: one function now holding two definitions of who
    owns a run.

    Driven through `_resumable`, which is what the banner calls. The previous
    test called `_journal_offers` directly and so entered one step past the bug.
    """
    import analyze_suspend
    from services import run_journal as J
    from services import suspend_record as SR

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    monkeypatch.setattr(SR, "record_path", lambda: tmp_path / "suspended.json")
    monkeypatch.setattr(analyze_suspend, "record_path",
                        lambda: tmp_path / "suspended.json", raising=False)
    out = tmp_path / "out"
    out.mkdir()

    class _Pane(analyze_suspend.AnalyzeSuspendMixin):
        def _current_output_dir(self):
            return str(out)

    def _both_records(queued: bool):
        J.open_run(out, input_dir=str(tmp_path / "in"), mode="local",
                   spec={"opts": {"raw": str(tmp_path / "in")}}, queued=queued)
        J.close_run(out, outcome="suspended")
        SR.save(SR.SuspendedRun(
            spec={"opts": {"raw": str(tmp_path / "in")}},
            input_dir=str(tmp_path / "in"), output_dir=str(out),
            suspended_at=1.0, queued=queued))
        return _Pane()._resumable()

    assert _both_records(queued=False) is not None, (
        "the fixture no longer produces an offer, so the assertion below would "
        "pass for the wrong reason")
    assert _both_records(queued=True) is None, (
        "the banner offered a plate the queue still holds — the suspend record "
        "is unfiltered, and `_resumable` ranks it ahead of the journal offer")


def test_a_queue_started_run_is_marked_as_the_queue_s(tmp_path, monkeypatch):
    """The flag is written by the run, not inferred by the reader."""
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    J.open_run(out, input_dir="/in", mode="local", spec={}, queued=True)
    assert J.read_journal(out).queued is True
    J.open_run(out, input_dir="/in", mode="local", spec={})
    assert J.read_journal(out).queued is False, (
        "the flag survived into a run the queue did not start")


def test_the_suspend_record_is_marked_by_the_writer_not_the_reader(
        tmp_path, monkeypatch):
    """The other writer of ``queued``, driven rather than hand-built.

    ``test_the_banner_asks_both_records_and_filters_both`` constructs a
    ``SuspendedRun(..., queued=…)`` itself, so it enters one step past the
    line that decides the flag — the same shape its own docstring warns about
    for the offer. Deleting ``queued=`` from ``_write_suspend_record`` fully
    restores the reported defect (the field then defaults False and the new
    filter never filters), and left the whole suspend-and-queue surface green.

    This drives the writer. The journal side already has its symmetric guard
    in ``test_a_queue_started_run_is_marked_as_the_queue_s``.
    """
    import analyze_suspend as AS

    written: list[object] = []
    monkeypatch.setattr(AS, "save", lambda run: written.append(run) or True)

    class _Spec:
        def to_json(self):
            return {"input_dir": "/in"}

    class _Edit:
        def text(self):
            return "/in"

    class _Runner:
        journal_queued = True

    class _Pane(AS.AnalyzeSuspendMixin):
        _input_edit = _Edit()
        _runner = _Runner()
        _well_done = 3
        _well_total = 12

        def _collect_run_spec(self):
            return _Spec()

        def _log_line(self, *a, **k):
            pass

    _Pane()._write_suspend_record(tmp_path / "out")

    assert written, "no suspend record was written"
    assert written[-1].queued is True, (
        "the suspend record does not say the job queue started this run, so "
        "the banner offers a plate the queue still holds and the same output "
        "directory is written by two runs at once")

    _Runner.journal_queued = False
    written.clear()
    _Pane()._write_suspend_record(tmp_path / "out")
    assert written[-1].queued is False, (
        "the flag is hardcoded rather than read from the run")
