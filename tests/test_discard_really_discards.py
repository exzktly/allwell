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

    entries = [e for e in J.unfinished_runs()
               if str(e.get("output_dir")) == str(out)]
    assert entries and entries[0].get("dismissed"), (
        "the journal still offers this run after Discard. The entry itself "
        "stays — it is what the incremental recovery reads — but it must carry "
        "the dismissal the offer honours")
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
    assert "run_journal.dismiss" in src and "clear(" in src, src
    body = src.split('"""')[-1]
    assert "if offer.from_journal" not in body and "else:" not in body, (
        "the forget still branches on where the offer came from, so one of "
        "the two pointers survives and the banner comes back")
    assert "run_journal.forget" not in body, (
        "`forget` deletes the index entry, which is also what the incremental "
        "recovery reads — see the test below")


# ── discarding an offer is not a claim that the plate finished ──────────────

def test_discard_stops_the_offer_without_disarming_the_recovery(tmp_path, monkeypatch):
    """Two behaviours read the same index entry with opposite intents.

    L4 made Discard drop both pointers. L1 made `resume_aware_spec` key on those
    same pointers to force `--incremental`. Deleting the entry therefore did two
    things: it stopped the banner asking, which the user wanted, and it disarmed
    the mid-flight-well recovery, which they did not — a user who dismissed the
    offer and pressed Run got the permanently-3-of-12 plate the forcing exists
    to prevent, with nothing said, while the record on disk still stated the
    plate was unfinished (§7.7 #1).

    "Do not ask me again" is about the banner; "this run did not finish" is
    about the plate.
    """
    import analyze_suspend
    from services import run_journal as J
    from services import suspend_record as SR

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    monkeypatch.setattr(SR, "record_path", lambda: tmp_path / "suspended.json")
    in_dir, out = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out.mkdir()
    J.open_run(out, input_dir=str(in_dir), mode="local",
               spec={"opts": {"raw": str(in_dir)}})      # left open: app killed

    class _Pane(analyze_suspend.AnalyzeSuspendMixin):
        def _current_output_dir(self):
            return str(out)

    pane = _Pane()
    offer = pane._resumable()
    assert offer is not None, "the fixture produced no offer to discard"
    assert pane._input_has_unfinished_work(str(in_dir)) is not None

    pane._forget_resumable(offer)

    assert pane._resumable() is None, "Discard did not stop the offer"
    assert pane._input_has_unfinished_work(str(in_dir)) is not None, (
        "Discard also disarmed the incremental recovery: the next Run will skip "
        "every well that was mid-flight as complete, and the record on disk "
        "still says this plate did not finish"
    )


def test_the_resume_message_names_how_the_run_actually_ended(tmp_path, monkeypatch):
    """One message for four causes was true a quarter of the time.

    Widening the predicate from the suspend record to the journal widened the
    set of causes with it: an open record means the app was killed, and a closed
    one can say `error` or `stopped` as readily as `suspended` (§7.7 #4).
    """
    import analyze_suspend
    from services import run_journal as J
    from services import suspend_record as SR

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    monkeypatch.setattr(SR, "record_path", lambda: tmp_path / "suspended.json")

    class _Pane(analyze_suspend.AnalyzeSuspendMixin):
        pass

    pane = _Pane()
    for i, (outcome, expected) in enumerate([
        ("error", "failed"), ("stopped", "stopped"), ("suspended", "suspended"),
        (None, "killed"),
    ]):
        in_dir, out = tmp_path / f"in{i}", tmp_path / f"out{i}"
        in_dir.mkdir()
        out.mkdir()
        J.open_run(out, input_dir=str(in_dir), mode="local", spec={})
        if outcome is not None:
            J.close_run(out, outcome=outcome)
        cause = pane._input_has_unfinished_work(str(in_dir))
        assert cause is not None, outcome
        assert expected in cause, (
            f"a run that ended {outcome!r} is described to the user as "
            f"{cause!r}")
