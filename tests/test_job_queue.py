"""Qt-free tests for services/job_queue.py.

JobQueue is pure bookkeeping — ordering, dependency gating, and outcome
recording — never a subprocess or QThread launcher. These tests exercise it
in isolation, with no runner and no Qt.
"""

from __future__ import annotations

import json

import pytest

from services.job_queue import JobQueue, JobSpec, JobState, load_queue, save_queue


def _job(job_id, *, chain_from=None, chain_target=None, chain_kind=None, state=JobState.PENDING):
    return JobSpec(
        id=job_id,
        kind="analyze",
        label=f"Analyze — {job_id}",
        spec={"opts": {"raw": f"/data/{job_id}"}},
        chain_from=chain_from,
        chain_target=chain_target,
        chain_kind=chain_kind,
        state=state,
    )


def test_next_runnable_returns_first_pending_in_order():
    q = JobQueue([_job("a"), _job("b")])
    assert q.next_runnable().id == "a"


def test_chained_job_not_runnable_until_predecessor_succeeds():
    q = JobQueue([_job("a"), _job("b", chain_from="a", chain_target="opts.raw", chain_kind="out_dir")])
    assert q.next_runnable().id == "a"

    q.mark_running("a")
    assert q.next_runnable() is None  # a is running, b is gated on it

    q.mark_finished("a", ok=True, produced={"out_dir": "/data/a/out"})
    assert q.next_runnable().id == "b"


def test_failed_predecessor_cascades_block_to_transitive_successors():
    q = JobQueue([
        _job("a"),
        _job("b", chain_from="a", chain_target="opts.raw", chain_kind="out_dir"),
        _job("c", chain_from="b", chain_target="opts.raw", chain_kind="out_dir"),
    ])
    q.mark_running("a")
    q.mark_finished("a", ok=False, message="boom")

    assert q.get("a").state == JobState.FAILED
    assert q.get("b").state == JobState.BLOCKED
    assert q.get("c").state == JobState.BLOCKED
    assert "predecessor a" in q.get("b").message
    assert q.next_runnable() is None


def test_cancelled_predecessor_also_cascades_block_not_failed():
    q = JobQueue([_job("a"), _job("b", chain_from="a")])
    q.mark_running("a")
    q.mark_finished("a", ok=False, message="stopped", cancelled=True)

    assert q.get("a").state == JobState.CANCELLED
    assert q.get("b").state == JobState.BLOCKED


def test_move_refuses_reorder_that_would_place_dependent_before_predecessor():
    q = JobQueue([_job("a"), _job("b", chain_from="a")])
    assert q.move("b", -1) is False  # would put b before a
    assert [j.id for j in q.jobs] == ["a", "b"]


def test_move_allows_reorder_that_keeps_chain_order_intact():
    q = JobQueue([_job("a"), _job("b"), _job("c", chain_from="a")])
    assert q.move("b", 1) is True  # swaps b and c; a stays before c
    assert [j.id for j in q.jobs] == ["a", "c", "b"]


def test_set_chain_rejects_self_chain():
    q = JobQueue([_job("a")])
    with pytest.raises(ValueError, match="itself"):
        q.set_chain("a", "a")


def test_set_chain_rejects_cycle():
    q = JobQueue([_job("a"), _job("b", chain_from="a")])
    with pytest.raises(ValueError, match="cycle"):
        q.set_chain("a", "b")


def test_materialise_resolves_chain_target_from_predecessor_output():
    q = JobQueue([
        _job("a"),
        _job("b", chain_from="a", chain_target="opts.raw", chain_kind="dataset_root"),
    ])
    q.mark_finished("a", ok=True, produced={"dataset_root": "/data/a"})
    spec = q.materialise("b")
    assert spec["opts"]["raw"] == "/data/a"
    # The persisted spec itself is untouched — materialise returns a copy.
    assert q.get("b").spec["opts"]["raw"] == "/data/b"


def test_materialise_raises_if_predecessor_output_not_yet_produced():
    q = JobQueue([
        _job("a"),
        _job("b", chain_from="a", chain_target="opts.raw", chain_kind="out_dir"),
    ])
    with pytest.raises(ValueError, match="hasn't been produced"):
        q.materialise("b")


def test_materialise_with_no_chain_returns_spec_copy():
    q = JobQueue([_job("a")])
    spec = q.materialise("a")
    assert spec == q.get("a").spec
    assert spec is not q.get("a").spec


def test_remove_clears_dangling_chain_links():
    q = JobQueue([_job("a"), _job("b", chain_from="a")])
    q.remove("a")
    assert q.get("b").chain_from is None


def test_json_round_trip_preserves_all_fields():
    q = JobQueue([
        _job("a", state=JobState.SUCCEEDED),
        _job("b", chain_from="a", chain_target="opts.raw", chain_kind="out_dir"),
    ])
    q.get("a").output_dir = "/data/a/out"
    encoded = q.to_json()
    reloaded = json.loads(json.dumps(encoded))
    restored = JobQueue.from_json(reloaded)

    assert [j.id for j in restored.jobs] == ["a", "b"]
    assert restored.get("a").state == JobState.SUCCEEDED
    assert restored.get("a").output_dir == "/data/a/out"
    assert restored.get("b").chain_from == "a"


def test_load_never_autostarts():
    q = JobQueue.from_json({"jobs": [_job("a").to_json()]})
    assert q.is_started is False


def test_load_marks_running_job_interrupted():
    q = JobQueue.from_json({"jobs": [_job("a", state=JobState.RUNNING).to_json()]})
    assert q.get("a").state == JobState.INTERRUPTED


def test_load_leaves_pending_job_alone():
    q = JobQueue.from_json({"jobs": [_job("a").to_json()]})
    assert q.get("a").state == JobState.PENDING


def test_an_interrupted_job_is_resumable_and_does_not_block_its_successors():
    """A job that was running when the app went away is not a failure.

    Whatever it had finished is on disk, and the pipeline's ``--incremental``
    reads that as its done-set, so the job is picked up where it left off the
    next time the queue is started — which is the point of persisting the queue
    across a session at all. Its successors therefore stay PENDING and wait for
    it, rather than cascading to BLOCKED as though the output they need were
    never coming.
    """
    data = {"jobs": [
        _job("a", state=JobState.RUNNING).to_json(),
        _job("b", chain_from="a", chain_target="opts.raw", chain_kind="out_dir").to_json(),
        _job("c", chain_from="b", chain_target="opts.raw", chain_kind="out_dir").to_json(),
    ]}
    q = JobQueue.from_json(data)
    assert q.get("a").state == JobState.INTERRUPTED
    assert q.get("b").state == JobState.PENDING
    assert q.get("c").state == JobState.PENDING
    assert "resumable" in q.get("a").message
    # Starting the queue resumes the interrupted job, not its successor.
    assert q.next_runnable().id == "a"


def test_a_suspended_job_keeps_its_place_and_is_picked_up_again():
    q = JobQueue.from_json({"jobs": [
        _job("a", state=JobState.RUNNING).to_json(),
        _job("b").to_json(),
    ]})
    q.mark_suspended("a")
    assert q.get("a").state == JobState.SUSPENDED
    assert q.next_runnable().id == "a", "suspended jobs resume before later ones run"


def test_suspending_does_not_block_a_chained_successor():
    """A suspend is not an outcome, so nothing downstream is written off."""
    q = JobQueue.from_json({"jobs": [
        _job("a", state=JobState.RUNNING).to_json(),
        _job("b", chain_from="a", chain_target="opts.raw", chain_kind="out_dir").to_json(),
    ]})
    q.mark_suspended("a")
    assert q.get("b").state == JobState.PENDING


def test_a_suspended_job_survives_a_save_load_round_trip():
    q = JobQueue.from_json({"jobs": [_job("a", state=JobState.RUNNING).to_json()]})
    q.mark_suspended("a")
    back = JobQueue.from_json(q.to_json())
    assert back.get("a").state == JobState.SUSPENDED
    assert back.next_runnable().id == "a"


def test_load_repairs_persisted_order_that_violates_chain_link():
    # b is stored before its predecessor a — a hand edit or a bug elsewhere.
    data = {"jobs": [
        _job("b", chain_from="a").to_json(),
        _job("a").to_json(),
    ]}
    q = JobQueue.from_json(data)
    ids = [j.id for j in q.jobs]
    assert ids.index("a") < ids.index("b")


def test_load_drops_links_on_genuine_cycle_in_persisted_data():
    # Both jobs chain from each other - not constructible via set_chain, but
    # a hand-edited file could still contain it.
    data = {"jobs": [
        _job("a", chain_from="b").to_json(),
        _job("b", chain_from="a").to_json(),
    ]}
    q = JobQueue.from_json(data)
    assert q.get("a").chain_from is None
    assert q.get("b").chain_from is None


def test_save_then_load_round_trips_through_disk(tmp_path):
    q = JobQueue([_job("a", state=JobState.SUCCEEDED), _job("b", chain_from="a")])
    q.get("a").output_dir = "/data/a/out"
    save_queue(q, tmp_path)

    assert (tmp_path / "job_queue.json").is_file()
    reloaded = load_queue(tmp_path)
    assert [j.id for j in reloaded.jobs] == ["a", "b"]
    assert reloaded.get("a").output_dir == "/data/a/out"
    assert reloaded.is_started is False


def test_load_queue_missing_file_returns_empty_queue(tmp_path):
    q = load_queue(tmp_path)
    assert q.jobs == []
    assert q.is_started is False


def test_load_queue_corrupt_file_returns_empty_queue(tmp_path):
    (tmp_path / "job_queue.json").write_text("{not json")
    q = load_queue(tmp_path)
    assert q.jobs == []


def test_load_queue_well_formed_json_but_malformed_queue_returns_empty_queue(tmp_path):
    """Regression guard: load_queue's try/except used to wrap only the
    json.loads() call -- JobQueue.from_json/JobSpec.from_json (a job entry
    missing "id"/"kind", or a "jobs" value that isn't a list of objects)
    raised KeyError/TypeError/AttributeError uncaught. load_queue runs
    unconditionally and synchronously from QueueBridge.__init__ at
    DatasetsTab construction, so a hand-edited or truncated-but-valid-JSON
    file crashed the whole app's startup, not just the queue page."""
    cases = [
        {"jobs": [{"kind": "subset", "label": "x"}]},  # entry missing "id"
        {"jobs": {"a": 1}},                             # "jobs" isn't a list
        {"jobs": "nope"},                                # "jobs" is a string
        [1, 2, 3],                                       # top level isn't a dict
    ]
    for i, payload in enumerate(cases):
        d = tmp_path / f"case{i}"
        d.mkdir()
        (d / "job_queue.json").write_text(json.dumps(payload))
        q = load_queue(d)
        assert q.jobs == [], f"case {i} ({payload!r}) should yield an empty queue, not raise"


# ── editing a queued job (update / duplicate / retry / clear) ─────────────────

def test_update_spec_replaces_the_stored_spec():
    q = JobQueue([_job("a")])
    assert q.update_spec("a", {"opts": {"raw": "/data/other"}}) is True
    assert q.get("a").spec == {"opts": {"raw": "/data/other"}}


def test_update_spec_refuses_the_running_job():
    """Its spec has already been handed to a runner; rewriting it would
    describe the in-flight run as something it is not."""
    q = JobQueue([_job("a", state=JobState.RUNNING)])
    assert q.update_spec("a", {"opts": {"raw": "/x"}}) is False
    assert q.get("a").spec == {"opts": {"raw": "/data/a"}}


def test_update_spec_leaves_state_alone():
    """Editing is not re-arming.

    Re-arming a succeeded job here would strand its successors: they already
    ran, and their predecessor would no longer read SUCCEEDED. retry() is the
    one explicit way a state moves backwards.
    """
    q = JobQueue([_job("a", state=JobState.SUCCEEDED)])
    q.update_spec("a", {"opts": {"raw": "/data/edited"}})
    assert q.get("a").state == JobState.SUCCEEDED


def test_duplicate_lands_directly_after_its_original():
    q = JobQueue([_job("a"), _job("b")])
    clone = q.duplicate("a", "a2")
    assert [j.id for j in q.jobs] == ["a", "a2", "b"]
    assert clone.state == JobState.PENDING


def test_duplicate_deep_copies_the_spec():
    """The copy must not share nested structure with its original — editing
    one would otherwise silently edit both."""
    q = JobQueue([_job("a")])
    clone = q.duplicate("a", "a2")
    clone.spec["opts"]["raw"] = "/data/changed"
    assert q.get("a").spec["opts"]["raw"] == "/data/a"


def test_duplicate_drops_the_chain_link():
    """A clone waiting on its twin's predecessor waits for output it has no
    relationship to. "Run this same tool against another dataset" is the
    reason to duplicate, and that job has no predecessor."""
    q = JobQueue([_job("a"), _job("b", chain_from="a", chain_target="opts.raw",
                                   chain_kind="out_dir")])
    clone = q.duplicate("b", "b2")
    assert clone.chain_from is None
    assert clone.chain_target is None and clone.chain_kind is None


def test_duplicate_refuses_an_id_already_in_use():
    q = JobQueue([_job("a"), _job("b")])
    assert q.duplicate("a", "b") is None
    assert [j.id for j in q.jobs] == ["a", "b"]


def test_retry_returns_a_failed_job_to_pending():
    q = JobQueue([_job("a", state=JobState.FAILED)])
    q.get("a").message = "boom"
    assert q.retry("a") is True
    assert q.get("a").state == JobState.PENDING
    assert q.get("a").message == ""


def test_retry_clears_a_succeeded_job_s_recorded_output():
    """A retried job has not produced anything yet. Leaving the old paths on
    it would let a chained successor materialise against the previous run's
    output while this one is still going."""
    q = JobQueue([_job("a", state=JobState.SUCCEEDED)])
    q.get("a").output_dir = "/out/old"
    q.retry("a")
    assert q.get("a").output_dir is None


def test_retry_unblocks_what_the_failure_stranded():
    q = JobQueue([_job("a"), _job("b", chain_from="a"), _job("c", chain_from="b")])
    q.mark_running("a")
    q.mark_finished("a", ok=False, message="boom")
    assert [j.state for j in q.jobs] == [JobState.FAILED, JobState.BLOCKED, JobState.BLOCKED]
    q.retry("a")
    assert [j.state for j in q.jobs] == [JobState.PENDING] * 3


def test_retry_leaves_jobs_blocked_by_a_different_failure():
    """Only the retried job's own successors move. A job blocked by a
    failure that has not been retried is still blocked."""
    q = JobQueue([_job("a"), _job("b", chain_from="a"),
                  _job("c"), _job("d", chain_from="c")])
    for failed in ("a", "c"):
        q.mark_running(failed)
        q.mark_finished(failed, ok=False, message="boom")
    q.retry("a")
    assert q.get("b").state == JobState.PENDING
    assert q.get("d").state == JobState.BLOCKED


@pytest.mark.parametrize("state", [JobState.PENDING, JobState.RUNNING])
def test_retry_refuses_a_job_that_is_not_finished(state):
    q = JobQueue([_job("a", state=state)])
    assert q.retry("a") is False
    assert q.get("a").state == state


def test_clear_terminal_drops_only_finished_jobs():
    q = JobQueue([_job("a", state=JobState.SUCCEEDED), _job("b"),
                  _job("c", state=JobState.FAILED),
                  _job("d", state=JobState.RUNNING)])
    assert q.clear_terminal() == 2
    assert [j.id for j in q.jobs] == ["b", "d"]


def test_clear_terminal_unlinks_a_survivor_chained_to_a_cleared_job():
    """Goes through remove() per id for exactly this: a surviving job left
    pointing at an id that is gone can never become runnable."""
    q = JobQueue([_job("a", state=JobState.SUCCEEDED),
                  _job("b", chain_from="a", chain_target="opts.raw",
                       chain_kind="out_dir")])
    q.clear_terminal()
    assert q.get("b").chain_from is None
    assert q.next_runnable().id == "b"


# ── move_to (drag reorder) ────────────────────────────────────────────────────

def test_move_to_shifts_the_rows_it_passes_rather_than_swapping():
    """The difference from move(): dragging picks a destination, so the jobs
    it passes shuffle up by one instead of one of them being exchanged."""
    q = JobQueue([_job("a"), _job("b"), _job("c"), _job("d")])
    assert q.move_to("a", 2) is True
    assert [j.id for j in q.jobs] == ["b", "c", "a", "d"]


def test_move_to_refuses_placing_a_job_before_its_predecessor():
    q = JobQueue([_job("a"), _job("b", chain_from="a")])
    assert q.move_to("b", 0) is False
    assert [j.id for j in q.jobs] == ["a", "b"]


@pytest.mark.parametrize("target", [-1, 5])
def test_move_to_refuses_an_index_off_the_end(target):
    q = JobQueue([_job("a"), _job("b")])
    assert q.move_to("a", target) is False
    assert [j.id for j in q.jobs] == ["a", "b"]


def test_move_to_is_a_no_op_on_its_own_index():
    q = JobQueue([_job("a"), _job("b")])
    assert q.move_to("a", 0) is False


# ── counts and repair notes ───────────────────────────────────────────────────

def test_counts_reports_every_state_including_the_empty_ones():
    """A caller formatting the summary never has to tell "none of these"
    apart from "no such state"."""
    q = JobQueue([_job("a"), _job("b"), _job("c", state=JobState.FAILED)])
    counts = q.counts()
    assert counts[JobState.PENDING] == 2
    assert counts[JobState.FAILED] == 1
    assert counts[JobState.SUCCEEDED] == 0


def test_repair_notes_survive_the_load_that_produced_them(tmp_path):
    """The repair used to be invisible: _repair_after_load returned notes and
    from_json dropped them, so a queue that came back interrupted said so
    nowhere the user could see."""
    q = JobQueue([_job("a", state=JobState.RUNNING)])
    save_queue(q, tmp_path)
    restored = load_queue(tmp_path)
    assert restored.get("a").state == JobState.INTERRUPTED
    assert any("interrupted" in note for note in restored.repair_notes)


def test_repair_notes_are_empty_for_a_clean_queue(tmp_path):
    save_queue(JobQueue([_job("a")]), tmp_path)
    assert load_queue(tmp_path).repair_notes == []


def test_retry_refuses_a_blocked_job():
    """Retrying a blocked job fixes nothing and hides why it is stuck.

    It never ran, and the predecessor that stopped it is still failed. Allowing
    it moved the job to PENDING and wiped the "predecessor a did not complete"
    message, leaving a queue that reported work waiting, did nothing when
    started, and no longer said what the problem was. Retry the ancestor.
    """
    q = JobQueue([_job("a"), _job("b", chain_from="a")])
    q.mark_running("a")
    q.mark_finished("a", ok=False, message="boom")
    assert q.get("b").state == JobState.BLOCKED

    assert q.retry("b") is False
    assert q.get("b").state == JobState.BLOCKED
    assert q.get("b").message, "the reason it is blocked must survive"
    assert q.next_runnable() is None

    # Retrying the ancestor is the move that works, and it frees the chain.
    assert q.retry("a") is True
    assert [j.state for j in q.jobs] == [JobState.PENDING, JobState.PENDING]


def test_repair_notes_are_taken_once_and_then_gone(tmp_path):
    """They describe what loading the file had to fix, which stops being true
    the moment the user acts on it — a note saying a job was interrupted must
    not still be on the page after that job has resumed and succeeded."""
    save_queue(JobQueue([_job("a", state=JobState.RUNNING)]), tmp_path)
    q = load_queue(tmp_path)
    assert q.take_repair_notes()
    assert q.take_repair_notes() == []
