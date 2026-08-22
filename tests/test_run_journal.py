"""The run journal's on-disk contract.

The journal is the only record All-Well writes *while* a run is happening, so
its whole value rests on two things being true: a run that dies mid-way leaves
a readable record of everything it got to, and a record that cannot be read
degrades to "no journal" rather than taking the Analyze pane down with it.

Both are pinned here, along with the one rule the module refuses to bend — a
well state that is not in ``WELL_STATES`` is rejected at the write, because a
typo'd state is a well that silently vanishes from every count that reads the
record back.

Pure logic: stdlib and ``services.run_journal`` only, no Qt and no imaging
stack.
"""

from __future__ import annotations

import json
import threading

import pytest

from services import run_journal as J

SPEC = {"opts": {"filename_schema": "experiment:well:fov:timepoint:channel",
                 "filename_sep": "_", "nuclear_token": "DAPI"}}


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Keep the index out of the developer's real state directory.

    ``index_path`` is the only thing the module writes outside the output
    folder, so redirecting it is enough — and it must be redirected, since a
    test that appends to a real ``run_index.json`` would have the app offering
    to resume a fixture the moment it next started.
    """
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


def _open(out_dir, **kw):
    kw.setdefault("spec", SPEC)
    kw.setdefault("mode", "local")
    kw.setdefault("input_dir", out_dir.parent / "in")
    return J.open_run(out_dir, **kw)


# ── the round trip ──────────────────────────────────────────────────────────

def test_a_run_round_trips_through_open_and_close(out_dir):
    _open(out_dir, argv=["--force"], expected_wells=["A01", "A02"])
    J.record_well(out_dir, "A01", "fetched")
    J.close_run(out_dir, outcome="ok", message="done")

    journal = J.read_journal(out_dir)
    assert journal is not None
    assert journal.outcome == "ok" and journal.message == "done"
    assert journal.argv == ("--force",)
    assert journal.expected_wells == ("A01", "A02")
    assert journal.spec == SPEC
    assert journal.wells["A01"].state == "fetched"


def test_an_open_record_is_how_a_dead_run_is_recognised(out_dir):
    """The distinction the whole feature exists for.

    A run that ends — however it ends — closes its record. One still open is a
    run the process did not survive, and nothing else on disk says so: the
    outputs of a killed run are indistinguishable from those of a run that
    finished with fewer wells.
    """
    _open(out_dir)
    assert J.read_journal(out_dir).is_open

    J.close_run(out_dir, outcome="stopped")
    assert not J.read_journal(out_dir).is_open


@pytest.mark.parametrize("outcome", J.RUN_OUTCOMES)
def test_every_outcome_survives_the_round_trip(out_dir, outcome):
    _open(out_dir)
    J.close_run(out_dir, outcome=outcome)
    assert J.read_journal(out_dir).outcome == outcome


def test_an_unknown_outcome_is_recorded_as_an_error(out_dir):
    """Never silently dropped: a closed record with no outcome would read as
    a run that ended cleanly, which is the one thing it must not do."""
    _open(out_dir)
    J.close_run(out_dir, outcome="banana")
    journal = J.read_journal(out_dir)
    assert journal.outcome == "error" and not journal.is_open


def test_opening_a_second_run_clears_the_first_runs_wells(out_dir):
    """The record describes *this* run. A previous run's per-well states left
    lying there would be read as this one's — the reader has no way to tell."""
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    _open(out_dir)
    assert J.read_journal(out_dir).wells == {}


# ── what it refuses ─────────────────────────────────────────────────────────

def test_an_unknown_well_state_is_refused(out_dir):
    _open(out_dir)
    with pytest.raises(ValueError, match="not a run-journal well state"):
        J.record_well(out_dir, "A01", "downloaded")


def test_recording_before_opening_writes_no_run_record(out_dir):
    """A well can be recorded into a folder with no run — it is one file per
    well — but the run record is not conjured to hold it."""
    assert J.record_well(out_dir, "A01", "fetched")
    assert J.read_journal(out_dir) is None


# ── degrading, rather than raising ──────────────────────────────────────────

def test_no_journal_reads_as_none(out_dir):
    assert J.read_journal(out_dir) is None


def test_a_record_from_another_version_reads_as_none(out_dir):
    _open(out_dir)
    path = out_dir / J.JOURNAL_DIRNAME / "run.json"
    payload = json.loads(path.read_text())
    payload["version"] = J.JOURNAL_VERSION + 99
    path.write_text(json.dumps(payload))
    assert J.read_journal(out_dir) is None


def test_a_truncated_record_reads_as_none_rather_than_raising(out_dir):
    _open(out_dir)
    (out_dir / J.JOURNAL_DIRNAME / "run.json").write_text('{"version": 1, "opened')
    assert J.read_journal(out_dir) is None


def test_an_unreadable_well_file_is_skipped_not_fatal(out_dir):
    """One corrupt per-well file must not cost the whole record. It is one
    file per well precisely so a single bad write is a single lost well."""
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    (out_dir / J.JOURNAL_DIRNAME / "wells" / "A02.json").write_text("{not json")
    journal = J.read_journal(out_dir)
    assert set(journal.wells) == {"A01"}


def test_a_well_file_naming_an_unknown_state_is_skipped(out_dir):
    """A record written by a future version that knows a state this one does
    not: dropping the well is honest, counting it under a state it is not is
    the silent substitution the guard on WELL_STATES exists to prevent."""
    _open(out_dir)
    wells = out_dir / J.JOURNAL_DIRNAME / "wells"
    wells.mkdir(parents=True, exist_ok=True)
    (wells / "A03.json").write_text(
        json.dumps({"well": "A03", "state": "teleported"}))
    assert J.read_journal(out_dir).wells == {}


# ── concurrency ─────────────────────────────────────────────────────────────

def test_concurrent_per_well_writes_do_not_corrupt_each_other(out_dir):
    """The reason for one file per well rather than one appended log.

    The orchestrator, the worker pool and the remote Fetcher thread all record
    without a lock. Two threads writing one file would interleave; two threads
    writing two files cannot.
    """
    _open(out_dir)
    wells = [f"A{i:02d}" for i in range(1, 25)]
    errors: list[BaseException] = []

    def _write(chunk):
        try:
            for well in chunk:
                J.record_well(out_dir, well, "fetched", reason="from a thread")
        except BaseException as exc:      # noqa: BLE001 — re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(wells[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    journal = J.read_journal(out_dir)
    assert set(journal.wells) == set(wells)
    assert all(r.state == "fetched" for r in journal.wells.values())


def test_a_later_state_replaces_an_earlier_one(out_dir):
    """The record says where a well *got to*, not every step it took — which
    is what lets the end-of-run retry correct a well it rescued."""
    _open(out_dir)
    J.record_well(out_dir, "A01", "not-fetched", reason="the pull failed")
    J.record_well(out_dir, "A01", "fetched", reason="recovered by the retry")
    record = J.read_journal(out_dir).wells["A01"]
    assert record.state == "fetched" and "retry" in record.reason


def test_wells_in_groups_states(out_dir):
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    J.record_well(out_dir, "A02", "analysed")
    J.record_well(out_dir, "A03", "failed")
    journal = J.read_journal(out_dir)
    assert journal.wells_in("analysed", "fetched") == ("A01", "A02")
    assert journal.wells_in("failed") == ("A03",)


# ── the remote address ──────────────────────────────────────────────────────

def _record_remote(out_dir, digest="d1", remote_out="/staging/runs/run-1/out"):
    return J.record_remote(
        out_dir, run_id="run-1", cache_key="k1", host_alias="hpc",
        remote_input_dir="/staging/datasets/k1",
        remote_output_dir=remote_out, argv_digest=digest)


def test_a_recorded_address_comes_back_for_the_same_configuration(out_dir):
    _open(out_dir)
    _record_remote(out_dir)
    stored = J.stored_remote_address(out_dir, argv_digest="d1")
    assert stored["remote_output_dir"] == "/staging/runs/run-1/out"
    assert stored["host_alias"] == "hpc"


def test_a_different_configuration_gets_no_address(out_dir):
    """Two runs whose pipeline arguments differ do not produce interchangeable
    output, so the earlier one's directory is not a place to re-attach to."""
    _open(out_dir)
    _record_remote(out_dir, digest="d1")
    assert J.stored_remote_address(out_dir, argv_digest="d2") is None


def test_no_remote_block_gets_no_address(out_dir):
    _open(out_dir)
    assert J.stored_remote_address(out_dir, argv_digest="d1") is None


def test_recording_an_address_without_a_run_does_nothing(out_dir):
    assert not _record_remote(out_dir)


# ── the index ───────────────────────────────────────────────────────────────

def test_an_open_run_is_listed_as_interrupted_and_a_closed_one_is_not(out_dir):
    _open(out_dir)
    assert [e["output_dir"] for e in J.interrupted_runs()] == [str(out_dir)]

    J.close_run(out_dir, outcome="ok")
    assert J.interrupted_runs() == []


def test_forget_drops_the_pointer_and_keeps_the_record(out_dir):
    """Discarding the offer must not discard the provenance: the journal is
    part of what the run produced, the index entry is only a reminder."""
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    J.forget(out_dir)
    assert J.interrupted_runs() == []
    assert J.read_journal(out_dir).wells["A01"].state == "fetched"


def test_a_corrupt_index_reads_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "state" / "idx.json"
    path.parent.mkdir(parents=True)
    path.write_text("[[[")
    monkeypatch.setattr(J, "index_path", lambda: path)
    assert J.interrupted_runs() == []


def test_re_running_the_same_folder_replaces_its_index_entry(out_dir):
    for _ in range(3):
        _open(out_dir)
    assert len(J.interrupted_runs()) == 1
