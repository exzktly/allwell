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
    _open(out_dir, argv=["--force"])
    J.record_well(out_dir, "A01", "fetched")
    J.close_run(out_dir, outcome="ok", message="done")

    journal = J.read_journal(out_dir)
    assert journal is not None
    assert journal.outcome == "ok" and journal.message == "done"
    assert journal.argv == ("--force",)
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


def test_recording_before_opening_writes_nothing_at_all(out_dir):
    """A well record belongs to a run, and the run record is never conjured.

    This used to accept the write and leave an orphan ``wells/A01.json`` in a
    folder with no ``run.json``. Nothing could read it back —
    :func:`read_journal` returns ``None`` without a run record — so it was a
    file that existed and could not be seen, and the same acceptance is what
    let a run write into a *previous*, closed run's wells when its own
    ``open_run`` failed. Refused outright now.
    """
    assert J.record_well(out_dir, "A01", "fetched") is False
    assert J.read_journal(out_dir) is None
    assert not list((J.journal_dir(out_dir) / "wells").glob("*.json"))


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

def _record_remote(out_dir, digest="d1", remote_out="/staging/runs/run-1/out",
                   source="src1"):
    return J.record_remote(
        out_dir, run_id="run-1", cache_key="k1", host_alias="hpc",
        remote_input_dir="/staging/datasets/k1",
        remote_output_dir=remote_out, argv_digest=digest, source_key=source)


def test_a_recorded_address_comes_back_for_the_same_configuration(out_dir):
    _open(out_dir)
    _record_remote(out_dir)
    stored = J.stored_remote_address(
        out_dir, argv_digest="d1", source_key="src1", host_alias="hpc")
    assert stored["remote_output_dir"] == "/staging/runs/run-1/out"
    assert stored["host_alias"] == "hpc"


def test_a_different_configuration_gets_no_address(out_dir):
    """Two runs whose pipeline arguments differ do not produce interchangeable
    output, so the earlier one's directory is not a place to re-attach to."""
    _open(out_dir)
    _record_remote(out_dir, digest="d1")
    assert J.stored_remote_address(
        out_dir, argv_digest="d2", source_key="src1", host_alias="hpc") is None


def test_replaced_images_get_no_address(out_dir):
    """The dangerous half, and the reason the argv digest alone is not enough.

    The upload cache key churns both when staging rewrites the archives (same
    images, new files) and when the user replaces the images. Keyed on the argv
    alone those are indistinguishable, so a plate re-exported with corrected
    images — no setting touched, Force not ticked — re-attached to the stale
    directory, the pre-flight check found it complete, the pipeline never ran,
    and the previous run's numbers came back reported as a successful run of
    the new data.
    """
    _open(out_dir)
    _record_remote(out_dir, source="images-v1")
    assert J.stored_remote_address(
        out_dir, argv_digest="d1", source_key="images-v2", host_alias="hpc") is None
    assert J.stored_remote_address(
        out_dir, argv_digest="d1", source_key="images-v1", host_alias="hpc") is not None


def test_an_unreadable_input_dir_gets_no_address(out_dir):
    """``source_fingerprint`` returns "" for an input it cannot read. That must
    decline rather than match a record that also happens to carry ""."""
    _open(out_dir)
    _record_remote(out_dir, source="")
    assert J.stored_remote_address(
        out_dir, argv_digest="d1", source_key="", host_alias="hpc") is None


def test_no_remote_block_gets_no_address(out_dir):
    _open(out_dir)
    assert J.stored_remote_address(
        out_dir, argv_digest="d1", source_key="src1", host_alias="hpc") is None


def test_recording_an_address_without_a_run_does_nothing(out_dir):
    assert not _record_remote(out_dir)


# ── the index ───────────────────────────────────────────────────────────────

def test_an_open_run_is_listed_as_unfinished_and_a_completed_one_is_not(out_dir):
    _open(out_dir)
    assert [e["output_dir"] for e in J.unfinished_runs()] == [str(out_dir)]

    J.close_run(out_dir, outcome="ok")
    assert J.unfinished_runs() == []


@pytest.mark.parametrize("outcome", J.UNFINISHED_OUTCOMES)
def test_a_run_that_did_not_complete_stays_listed(out_dir, outcome):
    """The gap this widening closes.

    Only a killed run used to qualify, so a run that *failed* offered nothing —
    even though the wells that got through are on disk exactly as they are
    after a crash, and an incremental re-run would skip them. A failure is the
    case where picking up matters most.
    """
    _open(out_dir)
    J.close_run(out_dir, outcome=outcome)
    assert [e["output_dir"] for e in J.unfinished_runs()] == [str(out_dir)]
    assert J.read_journal(out_dir).is_unfinished


def test_a_completed_run_is_not_unfinished(out_dir):
    _open(out_dir)
    J.close_run(out_dir, outcome="ok")
    journal = J.read_journal(out_dir)
    assert not journal.is_unfinished and not journal.is_open


def test_forget_drops_the_pointer_and_keeps_the_record(out_dir):
    """Discarding the offer must not discard the provenance: the journal is
    part of what the run produced, the index entry is only a reminder."""
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    J.forget(out_dir)
    assert J.unfinished_runs() == []
    assert J.read_journal(out_dir).wells["A01"].state == "fetched"


def test_a_corrupt_index_reads_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "state" / "idx.json"
    path.parent.mkdir(parents=True)
    path.write_text("[[[")
    monkeypatch.setattr(J, "index_path", lambda: path)
    assert J.unfinished_runs() == []


def test_re_running_the_same_folder_replaces_its_index_entry(out_dir):
    for _ in range(3):
        _open(out_dir)
    assert len(J.unfinished_runs()) == 1


# ── the rosters stay live ───────────────────────────────────────────────────

@pytest.mark.parametrize("roster", ["TALLY_GROUPS_LOCAL", "TALLY_GROUPS_REMOTE"])
def test_every_well_state_is_reported_somewhere(roster):
    """A state missing from a grouping is a well nobody is ever told about.

    §7.5 #10: pair every roster with a liveness test. Without one, adding a
    state to ``WELL_STATES`` and forgetting to group it produces no error and no
    output — the well is recorded, counted by nothing, and reported by nobody,
    which is exactly the silent omission the roster guard exists to prevent.

    There are two groupings and both are checked, because adding a state to one
    and not the other is the same omission with a mode attached.
    """
    # Resolved inside the test, not in the decorator: a name that does not exist
    # yet must fail this one test, not stop the module importing and take every
    # other guard in the file down with it.
    grouped = {state for _label, states in getattr(J, roster) for state in states}
    assert grouped == set(J.WELL_STATES), (
        f"ungrouped: {sorted(set(J.WELL_STATES) - grouped)}; "
        f"unknown: {sorted(grouped - set(J.WELL_STATES))}")


def test_the_two_groupings_differ_only_in_where_analysed_is_filed():
    """The one difference is the finding; anything else is drift.

    ``analysed`` means "the pipeline announced this well finished" in both
    modes. Locally that puts the CSV and out-zip on disk; remotely it puts them
    on the host, and only ``fetched`` says they arrived here.
    """
    def _by_state(groups):
        return {state: label for label, states in groups for state in states}

    local, remote = _by_state(J.TALLY_GROUPS_LOCAL), _by_state(J.TALLY_GROUPS_REMOTE)
    differing = {s for s in local if local[s] != remote.get(s)}
    assert differing == {"analysed"}, (
        f"the two groupings differ on {sorted(differing)}, not just 'analysed'")
    assert local["analysed"] == "finished"
    assert remote["analysed"] != "finished"
    assert remote["fetched"] == "finished"


def test_a_remote_run_does_not_call_an_undownloaded_well_finished(out_dir):
    """The reported defect, at the sentence a user actually reads.

    A remote run that died before reconciliation has wells the host computed
    and this machine never received. Filing those under "finished" erases the
    distinction the journal exists to draw — and it is the sentence the resume
    banner and the pre-run readout both print.
    """
    J.open_run(out_dir, input_dir="/in", mode="remote", spec={})
    J.record_well(out_dir, "A01", "fetched")
    J.record_well(out_dir, "A02", "analysed")
    tally = J.read_journal(out_dir).tally()
    assert "1 finished" in tally, tally
    assert "2 finished" not in tally, (
        f"a well the host computed and never sent was called finished: {tally}")
    assert "not downloaded" in tally, tally


def test_a_local_run_still_calls_an_analysed_well_finished(out_dir):
    """...and the local reading is unchanged, because locally it *is* finished."""
    J.open_run(out_dir, input_dir="/in", mode="local", spec={})
    J.record_well(out_dir, "A01", "analysed")
    J.record_well(out_dir, "A02", "analysed")
    assert "2 finished" in J.read_journal(out_dir).tally()


@pytest.mark.parametrize("state", J.WELL_STATES)
def test_a_well_in_any_state_reaches_the_tally(out_dir, state):
    _open(out_dir)
    J.record_well(out_dir, "A01", state)
    assert J.read_journal(out_dir).tally(), f"{state} is recorded and never said"


def test_the_tally_is_empty_when_nothing_was_recorded(out_dir):
    _open(out_dir)
    assert J.read_journal(out_dir).tally() == ""


def test_the_tally_groups_analysed_and_fetched_together(out_dir):
    """One well that finished locally and one fetched from a remote are both
    just "finished" to a user; the distinction is the journal's, not theirs."""
    _open(out_dir)
    J.record_well(out_dir, "A01", "analysed")
    J.record_well(out_dir, "A02", "fetched")
    assert "2 finished" in J.read_journal(out_dir).tally()


#: The calls that put a state into the record: the journal's own writer, and
#: the ``Fetcher`` wrapper that forwards to it with the best-effort guard
#: around it. Naming both is what keeps this test narrow — a scan wide enough
#: to catch every indirection is a scan wide enough to pass on prose.
_RECORDING_SEAMS = ("record_well", "_journal_well")


@pytest.mark.parametrize("state", J.WELL_STATES)
def test_every_well_state_is_written_by_some_caller(state):
    """A state nothing can produce is a stale roster entry.

    ``"uploaded"`` was exactly that: declared, documented, grouped for the
    tally, and written by no code path in the app — so a reader had a bucket
    that could never fill and a reviewer had a claim that read as covered.

    The scan is deliberately narrow. An earlier version of this test swept every
    string constant in any module that mentions ``record_well``, and passed for
    ``"uploaded"`` on the strength of a *phrase table* that happened to contain
    the word — a liveness test that could not fail, which is worse than none.
    Only two shapes count now: a literal handed to ``record_well`` or to the
    one-line wrapper that forwards to it, and the values of a dict literal (the
    ``well_stage -> state`` mapping ``analyze_progress`` looks the state up in).
    """
    import ast

    from tests._repo_scan import python_sources

    written: set[str] = set()
    for path in python_sources():
        if path.parts[-2:] == ("services", "run_journal.py"):
            continue
        text = path.read_text()
        if not any(seam in text for seam in _RECORDING_SEAMS):
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (getattr(node.func, "attr", None)
                        or getattr(node.func, "id", ""))
                if name in _RECORDING_SEAMS:
                    written.update(
                        a.value for a in node.args
                        if isinstance(a, ast.Constant)
                        and isinstance(a.value, str))
            elif isinstance(node, ast.Dict):
                written.update(
                    v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str))

    assert state in written, (
        f"no caller anywhere writes the {state!r} state; it is a roster entry "
        f"nothing can produce")


# ── a well never moves backwards ────────────────────────────────────────────

def test_a_fetched_well_is_not_downgraded_by_a_late_analysed(out_dir):
    """Two writers, no ordering between them, one well file.

    On a remote run ``analysed`` comes from a parsed stdout line drained by the
    GUI's poll timer, and ``fetched`` from the Fetcher thread polling the
    remote sidecar. A small well can download before the GUI queue drains, and
    the late ``analysed`` then overwrote a well that was already on disk — so
    the fetch report printed "analysed but not transferred" for a well the user
    could open.
    """
    _open(out_dir)
    J.record_well(out_dir, "A01", "analysed")
    J.record_well(out_dir, "A01", "fetched")
    J.record_well(out_dir, "A01", "analysed")          # the late one

    assert J.read_journal(out_dir).wells["A01"].state == "fetched"


def test_a_well_still_moves_forwards(out_dir):
    """The bound is one-directional, or the record never advances at all."""
    _open(out_dir)
    for state in ("uploaded", "analysed", "fetched"):
        J.record_well(out_dir, "A01", state)
        assert J.read_journal(out_dir).wells["A01"].state == state


@pytest.mark.parametrize("verdict", ["skipped", "failed", "not-fetched"])
def test_a_verdict_always_takes_effect(out_dir, verdict):
    """Only the upload→analysed→fetched path is ordered.

    The rest are verdicts rather than stages — written once, by whoever made
    the decision — and a rule that refused them would leave a well that failed
    to materialise on disk still recorded as fetched.
    """
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    J.record_well(out_dir, "A01", verdict, reason="because")

    rec = J.read_journal(out_dir).wells["A01"]
    assert rec.state == verdict and rec.reason == "because"


def test_a_failed_open_does_not_strip_the_record_it_could_not_replace(
        out_dir, monkeypatch):
    """Clearing the wells before writing lost the detail of a record that stayed.

    ``open_run``'s write is explicitly allowed to fail. Clearing first left the
    *previous* run's ``run.json`` beside an empty wells directory — a record
    still presenting as complete with its per-well detail silently gone.
    """
    _open(out_dir)
    J.record_well(out_dir, "A01", "fetched")
    J.close_run(out_dir, outcome="ok")
    monkeypatch.setattr(J, "_atomic_write_json", lambda *a, **kw: False)

    assert _open(out_dir) is None
    journal = J.read_journal(out_dir)
    assert journal is not None and journal.outcome == "ok"
    # Named rather than indexed, so reverting the fix fails with this sentence
    # instead of a bare ``KeyError`` — the failure *type* is part of the proof
    # (CLAUDE.md §7.5 #1).
    assert "A01" in journal.wells, (
        "the wells were dropped for a run.json that was never replaced")
    assert journal.wells["A01"].state == "fetched"


def test_a_run_records_its_own_wells_after_its_record_has_closed(out_dir):
    """The tail of every plate, and the reason this is a token and not a flag.

    ``analyze_progress`` writes ``analysed`` when the ``Well X … done.`` line is
    *parsed*, on the GUI thread, in bounded slices — while ``_close_journal``
    runs in the worker thread's ``finally`` the instant the subprocess exits.
    So a run's own completion lines arrive after its record is closed, and a
    rule that asked "is a run open here" refused all of them: a 24-well plate
    recorded four wells and the pre-run readout said so.
    """
    token = _open(out_dir)
    J.record_well(out_dir, "A01", "analysed", run_token=token)
    J.close_run(out_dir, outcome="ok")

    assert J.record_well(out_dir, "A02", "analysed", run_token=token) is True

    journal = J.read_journal(out_dir)
    assert set(journal.wells) == {"A01", "A02"}


def test_another_run_may_not_write_into_this_record(out_dir):
    """What the token is actually for.

    ``open_run``'s write is allowed to fail, and when it does the previous
    run's record and its wells are what remain. A writer belonging to the run
    that could not open one must not file its progress there — and with the
    no-regress rule, a ``fetched`` left by the earlier run would otherwise have
    silently refused the later one's ``uploaded``, so the journal reported a
    well as fetched for a run that had only uploaded it.
    """
    token = _open(out_dir)
    J.record_well(out_dir, "A01", "fetched", run_token=token)
    J.close_run(out_dir, outcome="ok")

    for state in ("uploaded", "fetched", "failed"):
        assert J.record_well(
            out_dir, "A01", state, run_token=token + 1.0) is False, state

    journal = J.read_journal(out_dir)
    assert journal.outcome == "ok"
    assert journal.wells["A01"].state == "fetched"


def test_a_failed_open_leaves_the_previous_record_whole_and_uncontradicted(
        out_dir, monkeypatch):
    """The two halves of the same commit, driven together.

    `test_a_failed_open_does_not_strip_the_record_it_could_not_replace` pins
    that the wells survive; this pins that the run which could not open one
    does not then write into them.
    """
    first = _open(out_dir)
    J.record_well(out_dir, "A01", "fetched", run_token=first)
    J.close_run(out_dir, outcome="ok")
    monkeypatch.setattr(J, "_atomic_write_json", lambda *a, **kw: False)
    # ``None``: this run has no record of its own, so its writers have no token
    # and — per ``analyze_progress`` — do not write at all.
    assert _open(out_dir) is None
    monkeypatch.undo()

    assert J.record_well(
        out_dir, "A01", "uploaded", run_token=first + 1.0) is False

    journal = J.read_journal(out_dir)
    assert journal.outcome == "ok" and journal.wells["A01"].state == "fetched", (
        "the failed run contradicted the record it could not replace")
