"""A failure record in ``errors/`` stops standing once its well is processed whole.

The report that started it: QC triage listed a well as ``CellposeUnavailable``
while Home showed the same well green and complete. ``errors/`` was never
cleared and a record carried no run identity, so a well that failed once and
was then re-run successfully kept its traceback beside a whole output, and each
reader guessed from file times whether it still applied. Validate & Repair did
not guess at all: it counted every file, and raised a ``recorded_failures``
error on the whole well.

``pipeline.error_records`` now archives a well's earlier runs' records into
``errors/superseded/`` at the moment its completion seal is written, and the
readers read the top level through ``current_records``. These pin:

* the user's sequence through the real ``main`` — a field fails, an incremental
  re-run recovers it, and the traceback leaves the top level for the archive;
* what retirement may and may not touch (this run's records, other wells,
  a note only a forced re-run resolves, records with no run identity);
* the remote mirror, and the readers' handling of records older than retirement.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.error_records as er
from pipeline.run_report import complete_seal_path, seal_well_complete, write_well_report
from tests.test_a_resumed_run_keeps_its_field_quality import (  # noqa: F401 — a fixture, used by name
    _plate, _run_main, stubbed_segmenter,
)


@pytest.fixture(autouse=True)
def _own_run_identity(monkeypatch):
    """``main`` sets the run identity in ``os.environ``; keep it from outliving the test."""
    monkeypatch.delenv(er.RUN_ID_ENV, raising=False)


def _record(out: Path, name: str, *, well: str = "", run: str = "", remedy: str = "",
            exc: str = "CellposeUnavailable", age: float = 0.0) -> Path:
    """A record as an earlier run (or an earlier build, with no headers) left it."""
    path = out / "errors" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [f"Well: {well}"] if well else []
    header += [f"Run: {run}"] if run else []
    header += [f"Remedy: {remedy}"] if remedy else []
    path.write_text("\n".join(header + [f"Exception type: {exc}", "", "Traceback ...", ""]))
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def _whole(out: Path, well: str, *, rewritten: bool = False) -> Path | None:
    write_well_report(out, well, groups_total=3, groups_covered=3)
    return seal_well_complete(out, well, rewritten=rewritten)


def _standing(out: Path) -> list[str]:
    return sorted(p.name for p in (out / "errors").glob("*.txt"))


def _archived(out: Path) -> list[str]:
    return sorted(p.name for p in (out / "errors" / "superseded").glob("*.txt"))


# ── the reported sequence, through the real pipeline ────────────────────────

@pytest.mark.usefixtures("stubbed_segmenter")
def test_a_field_that_failed_and_was_recovered_leaves_no_standing_traceback(tmp_path, monkeypatch):
    plate = _plate(tmp_path)
    out = plate / "out"

    _run_main(monkeypatch, plate, incremental=False, failing=("F002",))
    (trace,) = _standing(out)
    assert trace.startswith("C01_") and "F002" in trace
    assert not complete_seal_path(out, "C01").exists(), "the fixture's first run must leave the well partial"
    assert [r.well for r in er.current_records(out)] == ["C01"], "the partial well's failure must still stand"

    note = "C01_local_zip_unreadable.txt"
    er.write_note(out, "C01", note, "a download dropped images; re-run with --force")

    _run_main(monkeypatch, plate, incremental=True)
    assert complete_seal_path(out, "C01").is_file(), "the recovered well was not sealed"
    assert _standing(out) == [note], (
        "the well is whole, and the traceback of the field the re-run recovered still stands in errors/ — "
        "QC triage and Validate report a whole well as failed — or the incremental run retired a note only "
        "a forced re-run resolves")
    assert _archived(out) == [trace], "the superseded traceback was deleted rather than archived"
    assert [r.path.name for r in er.current_records(out)] == [note]


@pytest.mark.usefixtures("stubbed_segmenter")
def test_a_run_that_rewrites_the_well_retires_a_forced_run_s_note(tmp_path, monkeypatch):
    plate = _plate(tmp_path)
    out = plate / "out"
    er.write_note(out, "C01", "C01_local_csv_cut_short.txt", "a group was dropped; re-run with --force")
    _run_main(monkeypatch, plate, incremental=False)
    assert complete_seal_path(out, "C01").is_file()
    assert _standing(out) == [], "a run that rebuilt the well from scratch left the note it resolved standing"


@pytest.mark.usefixtures("stubbed_segmenter")
def test_a_re_run_that_fails_again_keeps_its_own_traceback(tmp_path, monkeypatch):
    plate = _plate(tmp_path)
    out = plate / "out"
    _run_main(monkeypatch, plate, incremental=False, failing=("F002",))
    first = _standing(out)
    _run_main(monkeypatch, plate, incremental=True, failing=("F002",))
    assert not complete_seal_path(out, "C01").exists()
    assert set(first) <= set(_standing(out)), "a well still partial lost the record of why"


# ── what retirement touches ─────────────────────────────────────────────────

def test_only_the_sealed_well_s_earlier_records_are_retired(tmp_path):
    out = tmp_path / "out"
    _record(out, "A01_pid1.txt", well="A01", run="earlier")
    _record(out, "A01_F002_pid1.txt", well="A01", run=er.current_run_id())
    _record(out, "A02_pid1.txt", well="A02", run="earlier")
    assert _whole(out, "A01") is not None
    assert _standing(out) == ["A01_F002_pid1.txt", "A02_pid1.txt"], (
        "retirement reached this run's own record, or another well's")
    assert _archived(out) == ["A01_pid1.txt"]


def test_a_partial_well_retires_nothing(tmp_path):
    out = tmp_path / "out"
    _record(out, "A01_pid1.txt", well="A01", run="earlier")
    write_well_report(out, "A01", groups_total=3, groups_covered=2)
    assert seal_well_complete(out, "A01", rewritten=True) is None
    assert _standing(out) == ["A01_pid1.txt"]


def test_a_note_only_a_forced_run_resolves_survives_an_incremental_seal(tmp_path):
    out = tmp_path / "out"
    er.write_note(out, "A01", "A01_local_zip_unreadable.txt", "images lost; re-run with --force")
    _record(out, "A01_local_csv_cut_short.txt", well="A01")       # an older build's note: no Remedy header
    _whole(out, "A01", rewritten=False)
    assert _standing(out) == ["A01_local_csv_cut_short.txt", "A01_local_zip_unreadable.txt"], (
        "an incremental run sealed the well without restoring what the note says is lost, and the note went")
    _whole(out, "A01", rewritten=True)
    assert _standing(out) == []
    assert _archived(out) == ["A01_local_csv_cut_short.txt", "A01_local_zip_unreadable.txt"]


def test_a_record_from_before_run_identity_is_read_by_its_file_name(tmp_path):
    out = tmp_path / "out"
    _record(out, "B3_exp_nuc_B3_f01_pid10.txt")          # no headers at all: well from the leading token
    _whole(out, "B03")
    assert _standing(out) == [] and _archived(out) == ["B3_exp_nuc_B3_f01_pid10.txt"]


def test_a_retired_name_already_in_the_archive_is_kept_beside_it(tmp_path):
    out = tmp_path / "out"
    _record(out, "A01_pid1.txt", well="A01", run="first")
    _whole(out, "A01")
    _record(out, "A01_pid1.txt", well="A01", run="second")
    _whole(out, "A01")
    assert _archived(out) == ["A01_pid1.1.txt", "A01_pid1.txt"]


def test_a_new_record_states_its_well_and_run(tmp_path, monkeypatch):
    monkeypatch.setenv(er.RUN_ID_ENV, "run-42")
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        path = er.write_fov_failure(tmp_path, "B3", "exp_nuc_B3_f01_T05", exc)
    rec = er.read_record(path)
    assert (rec.well, rec.run, rec.fov_group, rec.exception_type) == (
        "B03", "run-42", "exp_nuc_B3_f01_T05", "RuntimeError")
    assert "RuntimeError: boom" in path.read_text()


def test_begin_run_gives_each_run_a_new_identity():
    first = er.begin_run()
    assert er.current_run_id() == first
    assert er.begin_run() != first


# ── the remote mirror ───────────────────────────────────────────────────────

def test_the_local_copy_of_a_record_the_host_archived_is_removed(tmp_path):
    out = tmp_path / "out"
    host_archived = _record(out, "A01_pid1.txt", well="A01", run="r1")
    (out / "errors" / "superseded").mkdir()
    (out / "errors" / "superseded" / "A01_pid1.txt").write_bytes(host_archived.read_bytes())
    _record(out, "A02_pid1.txt", well="A02", run="r1")
    (out / "errors" / "superseded" / "A02_pid1.txt").write_text("Well: A02\nRun: r0\n\nolder\n")
    assert er.mirror_retirements(out) == 1
    assert _standing(out) == ["A02_pid1.txt"], "a record that only shares a name with an archived one was removed"


# ── readers: records written before retirement existed ──────────────────────

def test_a_record_older_than_its_well_s_seal_does_not_stand(tmp_path):
    out = tmp_path / "out"
    seal = complete_seal_path(out, "A01")
    seal.parent.mkdir(parents=True)
    seal.write_text("{}")
    _record(out, "A01_pid1.txt", well="A01", age=3600)              # before the seal: superseded
    _record(out, "A02_pid1.txt", well="A02", age=3600)              # no seal: stands
    _record(out, "A01_local_zip_unreadable.txt", well="A01", age=3600)   # a forced re-run's note: stands
    later = _record(out, "A01_pid2.txt", well="A01")
    os.utime(later, (seal.stat().st_mtime + 5,) * 2)                # a later attempt that failed: stands
    assert [r.path.name for r in er.current_records(out)] == [
        "A01_local_zip_unreadable.txt", "A01_pid2.txt", "A02_pid1.txt"]


def test_a_validate_scan_does_not_count_a_superseded_traceback(tmp_path):
    from datasets.validate.validate_compute import _scan_error_files

    out = tmp_path / "out"
    seal = complete_seal_path(out, "A01")             # sealed by a build from before records were archived
    seal.parent.mkdir(parents=True)
    seal.write_text("{}")
    _record(out, "A01_pid1.txt", well="A01", age=3600)
    _record(out, "A02_pid1.txt", well="A02", age=3600)
    assert _scan_error_files(SimpleNamespace(out_dir=out)) == {"A02": ("A02_pid1.txt",)}, (
        "Validate counted a traceback older than its well's completion seal as evidence against the well")


# ── QC triage ───────────────────────────────────────────────────────────────

def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_triage_lists_what_still_stands_and_says_when_it_was_written(tmp_path):
    from tests.test_qc_triage import _ctx, _run_check
    from review.features.home.triage import collect as tc

    started = time.time() - 600
    wells = {w: {"health": "complete", "failed": [], "missing": [], "missing_count": 0}
             for w in ("A01", "A02", "A03", "B01", "B02", "B03")}
    wells["A02"] = {"health": "partial", "failed": [], "missing": [["2", "0"]], "missing_count": 1}
    ctx = _ctx(tmp_path, {"run_check": _run_check(wells=wells, started_at=_iso(started))})
    _record(tmp_path, "A01_pid1.txt", well="A01", age=86400)    # earlier run, well whole since: hidden
    _record(tmp_path, "A02_pid1.txt", well="A02", age=86400)    # earlier run, well still partial: listed
    _record(tmp_path, "B01_pid1.txt", well="B01")               # this run, though the old output is whole
    rows = {r.well: r for r in tc.triage_rows(ctx) if r.kind == tc.KIND_ERROR_FILE}
    assert sorted(rows) == ["A02", "B01"], (
        "triage hid a still-partial well's earlier failure, or listed one its well has since superseded, "
        "or hid a failure written during the latest run")
    assert "earlier run" in rows["A02"].detail and "earlier run" not in rows["B01"].detail
    assert rows["A02"].detail.startswith("CellposeUnavailable — errors/A02_pid1.txt")
