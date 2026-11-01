"""A batched remote run uploads batch *i + 1* while batch *i* runs — one well in place of each well of batch *i*
that leaves the remote, never more.

Batches used to go strictly one after another — upload, run, upload, run — so every upload but the first was time
the node sat waiting. Sending the whole next batch ahead fixed that and doubled the remote's input, which is what the
batch size exists to bound; the user asked instead for each finished well removed from the server to be replaced by a
well of the next batch (``remote_batch_step._Ahead``, fed by ``Fetcher.on_well_removed``).

Wrong implementations these reject: a loop that still uploads only after the batch before has finished (the slot
test deadlocks against it and fails at its deadline); one that sends the whole next batch at once, or before any well
has left (the bound); one that counts a removal of a well from outside the running batch; a removal the batched
constructor never wires to the upload; an upload failure sent ahead that is reported over the batch still running,
or that loses that batch's result; an upload sent ahead and left running after a Stop, a suspend or a hand-off that
ends the run; and one sent after the last batch.
"""

from __future__ import annotations

import threading
import time
import types

from services.remote_batch_step import well_removed
from tests.test_batch_driver import _batched, _FakeProc, _stub_runner


def _start(tmp_path, monkeypatch, wells, upload, batch_size=1, before=None):
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, wells, batch_size=batch_size)
    _stub_runner(p, proc, ordering)
    p._runner._cache.upload_subset = upload
    if before is not None:
        before(p)
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    return proc, p, ordering, worker


def _wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def _recording_upload(sent):
    def upload(zips, dest, *, log_fn=None, **kw):
        sent.append((dest.rsplit("/", 1)[-1], [z.stem for z in zips]))
        for z in zips:
            log_fn(f"[remote] ↑ (1) {z.name}\n")           # what upload_subset says per archive
        return (len(zips) * 1024 * 1024, 0.0, 0)
    return upload


def _make_room(p, well):
    """Batch 1's *well* leaves the remote once the next batch's upload is waiting for room."""
    assert _wait_for(lambda: getattr(p, "_upload_ahead", None) is not None), "batch 2's upload never started"
    well_removed(p, well)


def _up(sent, staging):
    return [w for dest, wells in sent if dest == staging for w in wells]


def test_each_well_that_leaves_the_remote_makes_room_for_one_of_the_next_batch(tmp_path, monkeypatch):
    """Batch size 3, six wells. Nothing of batch 2 goes up until a well of batch 1 has left the remote; then exactly
    one per well removed, in order; a removal of a well outside the running batch frees nothing; and when batch 1
    ends, what is left of batch 2 goes up at once."""
    sent: list = []
    wells = ["A01", "A02", "A03", "A04", "A05", "A06"]
    proc, p, ordering, worker = _start(tmp_path, monkeypatch, wells, _recording_upload(sent), batch_size=3)
    assert _wait_for(lambda: getattr(p, "_upload_ahead", None) is not None), "batch 2's upload never started"
    time.sleep(0.3)
    assert _up(sent, "b2") == [], f"batch 2 went up before any well of batch 1 left the remote: {sent}"
    well_removed(p, "H12")                                 # not one of batch 1's: no room is made on the remote
    time.sleep(0.3)
    assert _up(sent, "b2") == []
    well_removed(p, "A02")
    assert _wait_for(lambda: _up(sent, "b2") == ["A04"]), f"one well up in place of A02: {sent}"
    well_removed(p, "A02")                                 # the same well again: still one slot
    time.sleep(0.3)
    assert _up(sent, "b2") == ["A04"]
    well_removed(p, "a01")
    assert _wait_for(lambda: _up(sent, "b2") == ["A04", "A05"]), sent
    proc.batch_finished(p, 1)                              # A03 never left; batch 1's end makes room for the rest
    assert _wait_for(lambda: _up(sent, "b2") == ["A04", "A05", "A06"]), sent
    proc.batch_finished(p, 2)
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive() and p._returncode == 0
    assert [d for d, _w in sent] == ["b1", "b2", "b2", "b2"], "nothing is sent after the last batch"
    log = "".join(p._logged)
    assert "batch 2/2: 3 well-zip(s) to upload while batch 1 runs, one as each of its wells leaves the remote" in log
    # Batch 1's upload is the run's; the ones sent ahead are marked so the progress view leaves the headline alone.
    assert "[remote] ↑ (1) A01.zip" in log
    assert "[remote] ⇡ (1) A04.zip" in log and "[remote] ⇡ (1) A06.zip" in log


def test_the_batched_constructor_wires_the_fetcher_s_removals_to_the_next_batch(tmp_path, monkeypatch):
    """The real ``_BatchedRemoteProcess.__init__`` and the real ``Fetcher._delete_well_files_on_remote``: a well
    whose remote files were deleted reaches the upload waiting for room, and one whose ``rm`` failed does not."""
    import services.remote_fetch as rf
    import services.remote_process as rp

    built = {}

    class _Fetcher:
        def __init__(self, host, run, **kw):
            built["fetcher"] = self
            self.host, self.run, self._log = host, run, (lambda _m: None)
            self.on_well_removed = None

        _delete_well_files_on_remote = rf.Fetcher._delete_well_files_on_remote

    monkeypatch.setattr(rp, "reattach_output_dir", lambda *a, fresh, **k: fresh)
    monkeypatch.setattr(rp, "_run_prepared", lambda *a, **k: None)
    monkeypatch.setattr(rp, "Fetcher", _Fetcher)
    monkeypatch.setattr(rp._BatchedRemoteProcess, "_run_batches", lambda self: None)
    runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(cache_key=lambda zipped: "k" * 32, _ssh_run=lambda *a, **k: None),
        _host_argv_extras=lambda _f: [],
        host=types.SimpleNamespace(remote_staging_dir="~/.allwell-staging", ssh_alias="h"))
    p = rp._BatchedRemoteProcess(runner, types.SimpleNamespace(well_zips=(), mode=None, incremental=False),
                                 tmp_path, ["--csv_prefix", "m"], 2, output_filter=None)
    freed: list[str] = []
    p._upload_ahead = types.SimpleNamespace(free=freed.append)
    fetcher = built["fetcher"]
    monkeypatch.setattr(rf, "delete_remote_paths", lambda *a, **k: True)
    fetcher._delete_well_files_on_remote("A01")
    monkeypatch.setattr(rf, "delete_remote_paths", lambda *a, **k: False)
    fetcher._delete_well_files_on_remote("A02")
    assert freed == ["A01"]


def test_an_upload_sent_ahead_that_fails_is_reported_after_the_batch_running_is_counted(tmp_path, monkeypatch):
    def upload(zips, dest, **kw):
        return (0, 0.0, 0 if dest.endswith("/b1") else 12)

    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02", "A03"], upload)
    assert _wait_for(lambda: any("ready" in o for o in ordering))
    time.sleep(0.2)                                   # batch 2's upload has failed by now, with batch 1 running
    assert not any("upload failed" in m for m in p._logged), "a failure was reported over the batch still running"
    proc.batch_finished(p, 1)
    worker.join(timeout=20)
    proc.finish()
    log = "".join(p._logged)
    assert "batch 1/3 complete" in log
    assert "ERROR batch 2 upload failed (rsync rc=12)" in log
    assert log.index("batch 1/3 complete") < log.index("ERROR batch 2 upload failed")
    assert p._returncode == 12


def test_a_stop_ends_the_upload_sent_ahead(tmp_path, monkeypatch):
    waiting = threading.Event()
    ended = threading.Event()

    def upload(zips, dest, *, stop_event=None, **kw):
        if dest.endswith("/b1"):
            return (0, 0.0, 0)
        waiting.set()
        stopped = stop_event.wait(timeout=30)
        ended.set()
        return (0, 0.0, 20 if stopped else 0)

    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02"], upload)
    _make_room(p, "A01")
    assert waiting.wait(10), "batch 2 was not sent ahead"
    p._stop.set()
    assert ended.wait(5), "the upload sent ahead did not end on Stop"
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive()


def test_a_run_that_ends_without_the_next_batch_stops_its_upload(tmp_path, monkeypatch):
    """The loop ends with no event the rsync watches — here, batch 1's hand-off is lost — and the batch sent ahead
    is not left uploading into a run that will never take it."""
    waiting = threading.Event()
    ended = threading.Event()

    def upload(zips, dest, *, stop_event=None, **kw):
        if dest.endswith("/b1"):
            return (0, 0.0, 0)
        waiting.set()
        stopped = stop_event.wait(timeout=30)
        ended.set()
        return (0, 0.0, 20 if stopped else 0)

    import services.remote_batch_step as step

    def lost(proc, *a, **k):                       # batch 1's well leaves, its upload starts, and the hand-off is lost
        well_removed(proc, "A01")
        waiting.wait(10)

    monkeypatch.setattr(step, "hand_off_and_wait", lost)
    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02"], upload)
    worker.join(timeout=20)
    proc.finish()
    assert ended.is_set(), "the run ended and left batch 2 uploading"
    assert p._upload_ahead is None


def test_a_suspend_during_a_batch_ends_the_upload_sent_ahead(tmp_path, monkeypatch):
    """Batch 2 is uploading ahead while batch 1 runs; Suspend lands. The upload must end then — before batch 1 has
    finished — not run to its end for a batch the loop will never send. Wrong implementation: an upload ahead that
    watches only Stop (or nothing), which a stub returning at once could not tell apart."""
    waiting = threading.Event()
    ended = threading.Event()

    def upload(zips, dest, *, stop_event=None, **kw):
        if dest.endswith("/b1"):
            return (0, 0.0, 0)
        waiting.set()
        stopped = stop_event.wait(timeout=30)
        ended.set()
        return (0, 0.0, 20 if stopped else 0)

    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02", "A03"], upload)
    _make_room(p, "A01")
    assert waiting.wait(10), "batch 2 was not sent ahead"
    p.note_suspend_requested()
    assert ended.wait(5), "the upload sent ahead ran on after Suspend, with batch 1 still running"
    proc._done = True
    proc.batch_finished(p, 1)
    proc.say("[remote] batch-driver: suspended — no further batches\n")
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive() and p._returncode == 0
    assert len([o for o in ordering if "ready" in o]) == 1, "a batch after the suspend was handed off"


def test_the_progress_view_does_not_read_an_upload_sent_ahead_as_the_run_uploading():
    """While batch 1 computes, batch 2's upload lines must move batch 2's wells' dials and nothing else: not the
    run's stage (the headline would read "Uploading k / N wells"), not the transfer label, not the status line."""
    from services.pipeline_runner import ProgressTracker
    from services.remote_batch_step import AHEAD_UP_MARK, _ahead_log
    from services.remote_wire import TRANSIENT_PREFIX

    said: list[str] = []
    log = _ahead_log(said.append)
    log("[remote] ↑ (1) A03.zip\n")
    log(f"{TRANSIENT_PREFIX}A03.zip 45% 12MB/s\n")
    assert said == [f"{AHEAD_UP_MARK} (1) A03.zip\n"], "rsync's live progress reached the status line"
    events = ProgressTracker().parse(said[0])
    assert ("well_stage", ("A03", "upload")) in events
    assert not [e for e in events if e[0] in ("stage", "status", "remote_xfer")], events
    plain = ProgressTracker().parse("[remote] ↑ (1) A03.zip\n")
    assert ("stage", "upload") in plain, "the upload the run waits for still drives the headline"


def test_a_stop_ends_an_upload_still_waiting_for_room(tmp_path, monkeypatch):
    """No well of batch 1 has left, so batch 2 has sent nothing and waits; Stop must end that wait too."""
    sent: list = []
    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02"], _recording_upload(sent))
    assert _wait_for(lambda: getattr(p, "_upload_ahead", None) is not None)
    ahead = p._upload_ahead
    p._stop.set()
    assert _wait_for(lambda: not ahead._thread.is_alive(), timeout=5), "the wait for room outlived Stop"
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive() and _up(sent, "b2") == []



def test_the_record_sent_with_each_well_describes_the_whole_batch(tmp_path, monkeypatch):
    """Each rsync rewrites the acquisition record and anchors beside the archives, scoped to what it is told; the
    driver hands the pipeline the last one. Sent a well at a time, the record must still name the whole batch, or
    the batch's earlier wells run with no recorded tile positions. Through the real ``carry_subset_record``."""
    import json
    from pathlib import Path

    import services.acquisition_transport as at

    monkeypatch.setattr(at, "_payload", lambda zipped, paths: {"wells": sorted(Path(p).stem for p in paths)})
    monkeypatch.setattr(at, "write_record_file", lambda path, record: Path(path).write_text(json.dumps(record)))
    records: list = []

    @at.carry_subset_record
    def upload_subset(cache, zips, dest, *, extra_sources=(), log_fn=None, **kw):
        for f in extra_sources:
            if f.name == at.RECORD_FILENAME:
                records.append((dest.rsplit("/", 1)[-1], [z.stem for z in zips], json.loads(f.read_text())["wells"]))
        return (1, 0.0, 0)

    def with_record(p):
        p._zipped = types.SimpleNamespace(**vars(p._zipped), nanowell_anchors=None)

    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02", "A03", "A04"],
                                       lambda zips, dest, **kw: upload_subset(None, zips, dest, **kw), batch_size=2,
                                       before=with_record)
    _make_room(p, "A01")
    assert _wait_for(lambda: any(d == "b2" for d, _z, _w in records))
    proc.batch_finished(p, 1)
    assert _wait_for(lambda: sum(1 for d, _z, _w in records if d == "b2") == 2)
    proc.batch_finished(p, 2)
    proc.finish()
    worker.join(timeout=20)
    b2 = [(sent, named) for d, sent, named in records if d == "b2"]
    assert b2 == [(["A03"], ["A03", "A04"]), (["A04"], ["A03", "A04"])]
