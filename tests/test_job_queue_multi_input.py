"""A queued job whose inputs come from more than one earlier job.

The motivating case: two CZI scans of one plate are converted, and the two
converted datasets are concatenated. The Concatenate job waits on both
conversions and takes dataset 1 from one and dataset 2 from the other. Each
test here is built so that the behaviour a single-link queue had — waiting on,
filling, blocking and unblocking through the *first* link only — gives a
different answer from the right one.
"""

from __future__ import annotations

import json

import pytest

from services.job_queue import ChainLink, JobQueue, JobSpec, JobState


def _conv(job_id, state=JobState.PENDING):
    return JobSpec(id=job_id, kind="czi_import", label=job_id,
                   spec={"source": f"/scans/{job_id}.czi",
                         "destination": f"/data/{job_id}"},
                   state=state)


def _concat(job_id="cat"):
    return JobSpec(id=job_id, kind="concatenate", label=job_id,
                   spec={"dataset1": "/planned/one", "dataset2": "/planned/two",
                         "destination": "/data/combined"})


def _two_inputs():
    q = JobQueue([_conv("a"), _conv("b"), _concat()])
    q.set_link("cat", "dataset1", "a", kind="dataset_root")
    q.set_link("cat", "dataset2", "b", kind="dataset_root")
    return q


def _succeed(q, job_id):
    q.mark_running(job_id)
    q.mark_finished(job_id, ok=True, produced={"dataset_root": f"/real/{job_id}"})


def _fail(q, job_id):
    q.mark_running(job_id)
    q.mark_finished(job_id, ok=False, message="boom")


def test_set_link_keeps_the_other_input():
    q = _two_inputs()
    assert [(link.source, link.target) for link in q.get("cat").links()] == [
        ("a", "dataset1"), ("b", "dataset2")]
    assert q.get("cat").sources() == ["a", "b"]


def test_unlinking_one_input_leaves_the_other():
    q = _two_inputs()
    q.set_link("cat", "dataset1", None)
    assert [(link.source, link.target) for link in q.get("cat").links()] == [("b", "dataset2")]


def test_waits_for_every_source_not_just_the_first():
    q = _two_inputs()
    _succeed(q, "a")
    assert q.next_runnable().id == "b"
    q.mark_running("b")
    assert q.next_runnable() is None, "ran Concatenate while dataset 2 was still being written"
    q.mark_finished("b", ok=True, produced={"dataset_root": "/real/b"})
    assert q.next_runnable().id == "cat"


def test_materialise_fills_both_inputs_from_their_own_sources():
    q = _two_inputs()
    _succeed(q, "a")
    _succeed(q, "b")
    spec = q.materialise("cat")
    assert spec["dataset1"] == "/real/a"
    assert spec["dataset2"] == "/real/b"
    assert spec["destination"] == "/data/combined"


def test_materialise_names_the_input_it_could_not_fill():
    q = _two_inputs()
    _succeed(q, "a")
    q.get("b").state = JobState.SUCCEEDED      # succeeded, but recorded nothing
    with pytest.raises(ValueError, match="dataset2"):
        q.materialise("cat")


def test_a_failure_of_the_second_source_blocks_the_job():
    q = _two_inputs()
    _succeed(q, "a")
    _fail(q, "b")
    assert q.get("cat").state == JobState.BLOCKED
    assert "predecessor b" in q.get("cat").message


def test_retrying_one_of_two_failed_sources_keeps_the_job_blocked():
    q = _two_inputs()
    _fail(q, "a")
    q.get("b").state = JobState.FAILED      # failed independently
    assert q.get("cat").state == JobState.BLOCKED
    assert q.retry("a")
    assert q.get("cat").state == JobState.BLOCKED, (
        "released a Concatenate whose dataset 2 can still never be written")
    assert q.retry("b")
    assert q.get("cat").state == JobState.PENDING


def test_a_cycle_through_an_ancestors_second_input_is_refused():
    # cat2 waits on a (dataset 1) and on cat (dataset 2). Linking cat to cat2
    # closes a cycle that only cat2's *second* link reveals.
    q = JobQueue([_conv("a"), _concat("cat"), _concat("cat2")])
    q.set_link("cat2", "dataset1", "a")
    q.set_link("cat2", "dataset2", "cat")
    with pytest.raises(ValueError, match="cycle"):
        q.set_link("cat", "dataset1", "cat2")
    assert q.get("cat").sources() == []


def test_order_check_sees_the_second_source():
    q = _two_inputs()
    # Moving the Concatenate above its second source must be refused.
    assert not q.move("cat", -1)
    assert [j.id for j in q.jobs] == ["a", "b", "cat"]


def test_removing_one_source_drops_only_its_link():
    q = _two_inputs()
    q.remove("a")
    assert [(link.source, link.target) for link in q.get("cat").links()] == [("b", "dataset2")]
    assert q.get("cat").chain_from == "b", "the remaining link must become the first one"


def test_both_links_survive_a_save_and_load():
    q = _two_inputs()
    restored = JobQueue.from_json(json.loads(json.dumps(q.to_json())))
    assert restored.get("cat").links() == q.get("cat").links()


def test_a_file_from_before_multi_input_links_loads_unchanged():
    data = {"jobs": [
        _conv("a").to_json(),
        {"id": "an", "kind": "analyze", "label": "an", "spec": {},
         "chain_from": "a", "chain_target": "opts.raw", "chain_kind": "dataset_root",
         "state": "pending"},
    ]}
    q = JobQueue.from_json(data)
    assert q.get("an").links() == [ChainLink("a", "opts.raw", "dataset_root")]


def test_an_older_reader_keeps_the_first_link():
    """What an earlier version reads from a file written now: the three
    single-link fields still hold the first link."""
    blob = _two_inputs().get("cat").to_json()
    assert (blob["chain_from"], blob["chain_target"]) == ("a", "dataset1")


def test_topological_repair_respects_every_source():
    q = _two_inputs()
    blob = q.to_json()
    # Stored with the Concatenate between its two sources.
    blob["jobs"] = [blob["jobs"][0], blob["jobs"][2], blob["jobs"][1]]
    restored = JobQueue.from_json(blob)
    assert [j.id for j in restored.jobs] == ["a", "b", "cat"]
    assert restored.repair_notes


def test_set_chain_still_replaces_every_link():
    q = _two_inputs()
    q.set_chain("cat", "a", chain_target="dataset1", chain_kind="dataset_root")
    assert q.get("cat").sources() == ["a"]
    q.set_chain("cat", None)
    assert q.get("cat").links() == []


# ── QueueBridge.set_link, the interface's way in ────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _bridge(tmp_path):
    from PySide6.QtCore import QObject, Signal

    from datasets.job_queue.controller import QueueBridge

    class _Runner(QObject):
        jobFinished = Signal(bool, str, str)

        def __init__(self):
            super().__init__()
            self.started: list[dict] = []

        def start_from_queue(self, spec):
            self.started.append(spec)

        def show_job(self, spec):
            pass

        def stop(self):
            pass

        def is_busy(self):
            return False

    bridge = QueueBridge(state_dir=tmp_path)
    runners = {kind: _Runner() for kind in ("czi_import", "concatenate")}
    for kind, runner in runners.items():
        bridge.register(kind, runner)
    for job in (_conv("a"), _conv("b"), _concat()):
        bridge.queue.add(job)
    return bridge, runners


def test_the_bridge_links_two_conversions_into_one_concatenate(qapp, tmp_path):
    bridge, runners = _bridge(tmp_path)
    assert bridge.set_link("cat", "dataset1", "a")
    assert bridge.set_link("cat", "dataset2", "b")
    bridge.start()
    runners["czi_import"].jobFinished.emit(True, "", "/real/a")
    runners["czi_import"].jobFinished.emit(True, "", "/real/b")
    started = runners["concatenate"].started
    assert len(started) == 1
    assert (started[0]["dataset1"], started[0]["dataset2"]) == ("/real/a", "/real/b")


def test_the_bridge_refuses_an_input_the_kind_does_not_have(qapp, tmp_path):
    bridge, _runners = _bridge(tmp_path)
    assert not bridge.set_link("cat", "source", "a")
    assert not bridge.set_link("cat", "dataset2", "nonexistent")
    assert bridge.queue.get("cat").links() == []


def test_the_bridge_persists_a_second_link(qapp, tmp_path):
    from services.job_queue import load_queue

    bridge, _runners = _bridge(tmp_path)
    bridge.set_link("cat", "dataset1", "a")
    bridge.set_link("cat", "dataset2", "b")
    assert load_queue(tmp_path).get("cat").sources() == ["a", "b"]
