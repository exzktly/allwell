"""A batched remote run uploads batch *i + 1* while batch *i* runs on the node.

Batches used to go strictly one after another — upload, run, upload, run — so every upload but the first was time
the node sat waiting. The driver only takes batch *i* once its ``b<i>`` directory is moved into ``in/``, so ``b<i+1>``
can fill meanwhile (``remote_batch_step._Ahead``).

Wrong implementations these reject: a loop that still uploads only after the batch before has finished (the first
test deadlocks against it and fails at its deadline); an upload failure sent ahead that is reported over the batch
still running, or that loses that batch's result; an upload sent ahead and left running after a Stop, a suspend or a
hand-off that ends the run; and one sent after the last batch.
"""

from __future__ import annotations

import threading
import time

from tests.test_batch_driver import _batched, _FakeProc, _stub_runner


def _start(tmp_path, monkeypatch, wells, upload):
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, wells)
    _stub_runner(p, proc, ordering)
    p._runner._cache.upload_subset = upload
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    return proc, p, ordering, worker


def _wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def test_the_next_batch_goes_up_before_the_one_running_has_finished(tmp_path, monkeypatch):
    sent: list[str] = []

    def upload(zips, dest, *, log_fn=None, **kw):
        sent.append(dest.rsplit("/", 1)[-1])
        log_fn(f"[remote] ↑ (1) {zips[0].name}\n")           # what upload_subset says per archive
        return (len(zips) * 1024 * 1024, 0.0, 0)

    proc, p, ordering, worker = _start(tmp_path, monkeypatch, ["A01", "A02", "A03"], upload)
    assert _wait_for(lambda: "b2" in sent), f"batch 2 was not sent while batch 1 ran: {sent}"
    assert "b3" not in sent, "only one batch is sent ahead: the remote holds two batches' input, not the plate"
    proc.batch_finished(p, 1)
    assert _wait_for(lambda: "b3" in sent), f"batch 3 was not sent while batch 2 ran: {sent}"
    proc.batch_finished(p, 2)
    proc.batch_finished(p, 3)
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive() and p._returncode == 0
    assert sent == ["b1", "b2", "b3"], "one upload per batch, the last with nothing sent after it"
    log = "".join(p._logged)
    assert "batch 2/3: uploading 1 well-zip(s) while batch 1 runs" in log
    assert "batch 3/3: uploading 1 well-zip(s) while batch 2 runs" in log
    # Batch 1's upload is the run's; the ones sent ahead are marked so the progress view leaves the headline alone.
    assert "[remote] ↑ (1) A01.zip" in log
    assert "[remote] ⇡ (1) A02.zip" in log and "[remote] ⇡ (1) A03.zip" in log


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

    monkeypatch.setattr(step, "hand_off_and_wait", lambda proc, *a, **k: (waiting.wait(10), None)[1])
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
