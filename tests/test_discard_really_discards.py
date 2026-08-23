"""Discarding a resume offer must silence it, not one of the two records.

A plate that was suspended leaves **two** pointers behind: a suspend record
(`services.suspend_record`) and an entry in the run journal's index. The banner
is built from whichever it finds, and `_forget_resumable` dropped only the one
the offer happened to be sourced from.

So Discard cleared the banner, the next refresh read the surviving record, and
the banner came straight back — with nothing the user could do to make it stop.
Their answer was recorded against one bookkeeping file while the question was
being asked by two.

Neither record is the user's data: the output folder is the work, and both of
these are only pointers to it. Dropping both is what "stop asking me about
this" means.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("PySide6")

from services import run_journal as J  # noqa: E402
from services import suspend_record  # noqa: E402


def test_both_records_are_dropped_not_just_the_offers_own(tmp_path, monkeypatch):
    """Driven against the two real records, in the state a suspend leaves."""
    import analyze_suspend

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    store = tmp_path / "state" / "suspended_runs.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(suspend_record, "record_path", lambda: store)

    out = tmp_path / "out"
    out.mkdir()
    in_dir = tmp_path / "in"
    in_dir.mkdir()

    # The state a suspended run really leaves: BOTH pointers.
    J.open_run(out, spec={}, mode="local", input_dir=in_dir)
    known_fields = {f: {"output_dir": str(out), "input_dir": str(in_dir),
                        "spec": {}, "suspended_at": 1.0}.get(f, 0)
                    for f in suspend_record.SuspendedRun.__dataclass_fields__}
    suspend_record.save(suspend_record.SuspendedRun(**known_fields))
    assert any(str(e.get("output_dir")) == str(out) for e in J.unfinished_runs())
    assert suspend_record.load_all(), "the suspend record fixture did not take"

    # Built through the dataclass' real field list rather than a guessed
    # signature: a constructor that silently accepted the wrong keywords would
    # make this test vacuous.
    known = {"output_dir": str(out), "input_dir": str(in_dir),
             "spec": {}, "from_journal": True}
    offer = analyze_suspend._Resumable(
        **{f: known.get(f) for f in analyze_suspend._Resumable.__dataclass_fields__})

    analyze_suspend.AnalyzeSuspendMixin._forget_resumable(object(), offer)

    assert not any(str(e.get("output_dir")) == str(out)
                   for e in J.unfinished_runs()), (
        "the journal still offers this run after Discard")
    assert not suspend_record.load_all(), (
        "the suspend record survived a Discard sourced from the journal, so "
        "the next refresh reads it and the banner comes straight back — the "
        "user's answer went to one bookkeeping file while two were asking")


def test_the_forget_calls_both_records_unconditionally():
    """The structural half: no branch decides which pointer is dropped.

    `from_journal` still decides which record *sourced* the offer — the
    banner's wording depends on it — but that is not a claim the other record
    does not exist, and using it to choose what to delete is what left one
    behind.
    """
    import analyze_suspend

    src = inspect.getsource(
        analyze_suspend.AnalyzeSuspendMixin._forget_resumable)
    assert "run_journal.forget" in src and "clear(" in src, src
    body = src.split('"""')[-1]
    assert "if offer.from_journal" not in body and "else:" not in body, (
        "the forget still branches on where the offer came from, so one of "
        "the two pointers survives and the banner comes back")
