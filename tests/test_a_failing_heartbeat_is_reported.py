"""A heartbeat that is not reaching the host says so, before the host acts on the silence.

The run's heartbeat is one ``ssh touch`` every 30 s, and its result was
discarded. A failing heartbeat was therefore invisible until the remote gave
the node back (120 s without a beat, between batches) or suspended the batch in
flight (600 s), and the remote's message then blamed the app — the review
measured 7–12 failed touches with nothing logged. Now the second miss in a row
is a WARNING and the third an ERROR naming when the host will act.
"""

from __future__ import annotations

import threading
import time
import types

from services.remote_heartbeat import _UploadHeartbeat
from services.remote_wait_snippets import MID_BATCH_HEARTBEAT_STALE_S, UPLOAD_HEARTBEAT_STALE_S


class _Host:
    """``touch`` answering from a script of return codes."""

    def __init__(self, codes):
        self.codes = list(codes)
        self.asked: list[list[str]] = []

    def _ssh_run(self, argv, *, check, **kw):
        self.asked.append(list(argv))
        return types.SimpleNamespace(returncode=self.codes.pop(0), stdout="", stderr="")


def _at(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


def test_the_second_miss_warns_and_the_third_names_when_the_node_goes():
    logged: list[str] = []
    now = {"t": 1_000_000.0}
    hb = _UploadHeartbeat(_Host([0, 255, 124, 255, 255]), "/r/run/.aw_upload_alive",
                          log_fn=logged.append, batched=True, clock=lambda: now["t"])
    hb.beat()                       # lands at t
    last_ok = now["t"]
    for _ in range(2):
        now["t"] += 30
        hb.beat()
    assert logged == ["[remote] WARNING the run's heartbeat did not reach the host 2 times in a row (ssh exit 124) "
                      "— still trying\n"]
    now["t"] += 30
    hb.beat()
    assert logged[1] == (
        f"[remote] ERROR the run's heartbeat has not reached the host 3 times in a row (ssh exit 255). If it does "
        f"not recover, the host takes the app for gone: a node waiting for a batch is given back at about "
        f"{_at(last_ok + UPLOAD_HEARTBEAT_STALE_S)} ({UPLOAD_HEARTBEAT_STALE_S} s after the last beat that landed); "
        f"a batch already running is suspended at about {_at(last_ok + MID_BATCH_HEARTBEAT_STALE_S)} "
        f"({MID_BATCH_HEARTBEAT_STALE_S // 60} min)\n")
    now["t"] += 30
    hb.beat()
    assert len(logged) == 2, "said once per run of misses, not at every beat after"


def test_a_recovered_heartbeat_says_so_and_starts_counting_again():
    logged: list[str] = []
    hb = _UploadHeartbeat(_Host([255, 255, 0, 255, 0]), "/r/hb", log_fn=logged.append)
    for _ in range(5):
        hb.beat()
    assert logged[-1] == "[remote] the heartbeat is reaching the host again after 2 missed beat(s)\n"
    assert len(logged) == 2, "a single miss after recovery is a blip, not a warning"


def test_a_single_shot_run_s_error_names_only_the_upload_wait():
    logged: list[str] = []
    hb = _UploadHeartbeat(_Host([255, 255, 255]), "/r/hb", log_fn=logged.append)
    for _ in range(3):
        hb.beat()
    assert "a node waiting for the upload is given back" in logged[-1]
    assert "batch already running" not in logged[-1]


def test_the_batched_run_wires_its_log_into_the_heartbeat(tmp_path, monkeypatch):
    """Through the real `_start_driver`: the heartbeat it starts reports into the run's own log."""
    from tests.test_batch_driver import _FakeProc, _batched, _stub_runner

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    beats = {"fail": False}
    real = p._runner._cache._ssh_run

    def ssh_run(argv, *, check, **kw):
        if argv[0] == "touch" and str(argv[-1]).endswith(".aw_upload_alive") and beats["fail"]:
            return types.SimpleNamespace(returncode=255, stdout="", stderr="")
        return real(argv, check=check, **kw)

    p._runner._cache._ssh_run = ssh_run
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while p._heartbeat is None and time.monotonic() < deadline:
        time.sleep(0.01)
    beats["fail"] = True
    for _ in range(3):
        p._heartbeat.beat()
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)
    log = "".join(p._logged)
    assert "WARNING the run's heartbeat did not reach the host 2 times in a row" in log
    assert "ERROR the run's heartbeat has not reached the host 3 times in a row" in log
    assert "a batch already running is suspended" in log
