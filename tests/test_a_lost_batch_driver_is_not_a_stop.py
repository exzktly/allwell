"""A batch driver that went away is a failure with a reason, not the user's Stop.

The batch loop learns that its driver is gone — a walltime expiry, a node
failure, the driver's own exit 75, a hand-off the app gave up on — from the
driver's stream ending with batches unreported. It used to unblock itself by
setting its stop event, and that event *is* ``PipelineRunner._stop_requested``
(the real ``spawn`` is handed it). So every lost driver was reported as the
user's Stop: amber "Cancelled — stopped", the journal recording ``stopped``, the
Job Queue marking the job cancelled and pausing the whole queue — and the
Fetcher cancelled, leaving the finished batches' wells on the remote.

These drive the real ``PipelineRunner._run_pipeline_remote`` over the real
``_BatchedRemoteProcess._run_batches``, with only the driver ssh and the host's
commands stood in (``tests/test_batch_driver.py``), in the order the app runs
them: ``spawn`` is handed the runner's stop event, starts the batch loop, and
the runner reads the loop's stream to its end.
"""

from __future__ import annotations

import threading
import time
import types

from tests.test_batch_driver import _FakeProc, _batched, _stub_runner


def _run(tmp_path, monkeypatch, *, driver_lines, wells=("A01", "A02", "A03"), stop_mid_run=False):
    """Run the real remote path once; return the runner, its events and the batched process.

    The driver says *driver_lines* once batch 1 is handed to it, and its stream ends once batch 2 is: the node went
    away mid-batch-2 — or, with *stop_mid_run*, the user pressed Stop there.
    """
    import services.pipeline_runner as pr

    zipped = types.SimpleNamespace(
        up_to_date_wells=(), well_zips=(), incremental=False, mode=types.SimpleNamespace(value="zip"),
        cleanup=None)
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    proc = _FakeProc()
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    p = _batched(tmp_path, list(wells))
    asked: list[str] = []
    _stub_runner(p, proc, asked)
    p._runner.close_remote_job = lambda **k: None
    r = pr.PipelineRunner()

    def handed(i):
        deadline = time.monotonic() + 10
        while not any(a.startswith("touch") and a.endswith(f".aw_batch_{i}_ready") for a in asked):
            assert time.monotonic() < deadline, f"batch {i} was never handed over: {asked}"
            time.sleep(0.01)

    def driver():
        handed(1)
        for line in driver_lines:
            proc.say(line)
        handed(2)
        if stop_mid_run:
            r._stop_requested.set()     # the user's Stop, as `PipelineRunner.stop` delivers it: the event first,
        proc.finish()                   # then the ssh's death — or, without it, the allocation simply ending

    def spawn(*_a, stop_event=None, **_k):
        # What `RemoteRunner.spawn` does with the event: the batched process's own stop flag.
        p._stop = stop_event
        p._worker = threading.Thread(target=p._run_batches, daemon=True)
        p._worker.start()
        threading.Thread(target=driver, daemon=True).start()
        return p

    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn,
        verify_and_cleanup=lambda ok, cleanup_mode, **k: ok,
        close_remote_job=lambda **k: None,
        host=types.SimpleNamespace(compute_launcher=""))
    opts = {"filename_schema": "a:b", "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI"}
    r._run_pipeline_remote(tmp_path / "in", tmp_path / "out", [], opts)
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return r, events, p


def _errors(events):
    return [e[1] for e in events if e[0] == "error"]


def test_a_driver_lost_mid_run_fails_with_its_reason_and_is_not_a_stop(tmp_path, monkeypatch):
    r, events, p = _run(tmp_path, monkeypatch, driver_lines=[
        "Well A01 — done.\n",
        "[remote] batch-driver: batch 1 finished rc=0\n"])

    errors = _errors(events)
    assert len(errors) == 1, events
    assert errors[0].startswith("[remote] ERROR the allocation ended"), errors[0]
    assert "batch 2/3" in errors[0], errors[0]
    assert "(remote pipeline exited with code 1)" in errors[0], errors[0]
    # Not the user's Stop, by every reading the GUI, the journal and the Job Queue make of it.
    assert r.was_stopped is False, "a lost driver was reported as the user's Stop"
    assert r.cancel_state_at_failure == (False, False)
    assert r._journal_outcome() == "error"
    # The finished batch's wells are still on the remote: the downloads are left to drain.
    assert p._cancelled == [], "the Fetcher was cancelled with finished wells still on the remote"


def test_the_user_s_stop_is_still_a_stop(tmp_path, monkeypatch):
    """The control: the same stream ending, with the Stop pressed first, is the user's."""
    r, events, _p = _run(tmp_path, monkeypatch, stop_mid_run=True, driver_lines=[
        "Well A01 — done.\n",
        "[remote] batch-driver: batch 1 finished rc=0\n"])
    assert r.was_stopped is True
    assert r.cancel_state_at_failure[0] is True
    assert r._journal_outcome() == "stopped"
