"""A run whose journal never opened must not close the one before it.

`open_run`'s write is allowed to fail — a full disk, a read-only mount, a
network share that dropped — and when it does the *previous* run's `run.json`
is what remains in that output directory. `record_well` already refuses to
write into a record that is not its own, and states why at length. `close_run`
had no such check.

So the sequence that matters is: run A is killed, leaving its record open and
its index entry holding it as resumable; run B starts against the same output
directory, its `open_run` fails, and run B's exit writes `closed_at` and
`outcome: ok` over run A's record. The offer to resume A disappears, for a
plate that never finished — and nothing anywhere says so.

`test_run_journal_reattach.py::test_the_stored_address_survives_the_app_s_real_call_order`
is the shape this follows: drive the order the app runs, not the unit's API.
"""
from __future__ import annotations

import inspect

import pytest

from services import run_journal as J


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    d = tmp_path / "out"
    d.mkdir()
    return d


def test_a_token_from_another_run_cannot_close_this_record(out_dir, tmp_path):
    token_a = J.open_run(out_dir, spec={}, mode="local", input_dir=tmp_path / "in")
    assert token_a, "open_run returned no token, so nothing below measures anything"

    # Run B's own open_run failed, so it holds a token that is not on disk.
    assert J.close_run(out_dir, outcome="ok", run_token=token_a + 1.0) is False

    rec = J.read_journal(out_dir)
    assert rec is not None and rec.closed_at is None, (
        "run A's record was closed by a run that never opened its own, so the "
        "killed plate is no longer offered for resume")


def test_the_runs_own_token_still_closes_it(out_dir, tmp_path):
    """The baseline. Without it a `close_run` that refused everything would
    pass the test above while never closing any record at all."""
    token = J.open_run(out_dir, spec={}, mode="local", input_dir=tmp_path / "in")

    assert J.close_run(out_dir, outcome="ok", run_token=token) is True
    rec = J.read_journal(out_dir)
    assert rec is not None and rec.closed_at is not None


def test_no_token_still_closes(out_dir, tmp_path):
    """`None` means "I cannot tell which run this is", and must not start
    refusing — the parameter is a guard for callers that have a token, not a
    new requirement on those that do not."""
    J.open_run(out_dir, spec={}, mode="local", input_dir=tmp_path / "in")
    assert J.close_run(out_dir, outcome="ok") is True


def test_the_runner_passes_its_own_token(out_dir):
    """§7.7 #1 — the guard is worth nothing if the one caller omits it."""
    from services.pipeline_runner import PipelineRunner

    src = inspect.getsource(PipelineRunner._close_journal)
    assert "run_token" in src, (
        "_close_journal calls close_run without a token, so the refusal can "
        "never fire on the path it exists for")
    assert "_journal_token" in src


def test_close_run_and_record_well_ask_the_same_question():
    """One rule, not two. They are the two writers into one record, and a
    difference between them is a difference nobody would notice until a
    killed plate stopped being offered."""
    close_src = inspect.getsource(J.close_run)
    well_src = inspect.getsource(J.record_well)
    for src, name in ((close_src, "close_run"), (well_src, "record_well")):
        assert 'float(payload.get("opened_at") or 0.0) != run_token' in src, (
            f"{name} does not compare the token against the record's own "
            f"opened_at, so the two writers disagree about which run owns it")
