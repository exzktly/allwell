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
