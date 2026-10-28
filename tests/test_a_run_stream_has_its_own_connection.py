"""A run's output stream rides a connection of its own, never the shared master.

The heartbeat, the batch hand-off and every other control command share one
ssh master (``ControlMaster=auto``). The run's output stream — the batch
driver's, or the single-shot pipeline's — used to share it too. Everything
multiplexed through a master ends with it, so a master that stopped answering
mid-batch took the stream down with the heartbeat: the run log showed a TIF
being written, then the heartbeat's "has not reached the host 3 times in a row
(ssh exit 124)", then a lost driver, while a fresh ssh to the same host worked.
``recover_dead_master``'s ``ssh -O exit`` — the remedy for a hung master — would
end the stream the same way, and through the PTY's SIGHUP the batch on the node.
"""

from __future__ import annotations

import threading
import time
import types

from services.remote_config import RemoteHost
from services.remote_heartbeat import _UploadHeartbeat


def _posix(monkeypatch) -> None:
    monkeypatch.setattr("services.remote_config._is_windows", lambda: False)


def _control_options(argv: list[str]) -> list[str]:
    """Every ``-o Control…`` value in *argv*, in order."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1])
            if a == "-o" and argv[i + 1].startswith("Control")]


def test_an_unshared_ssh_opts_out_of_the_master_explicitly(monkeypatch):
    """Explicitly, so a ControlMaster in the user's own ~/.ssh/config cannot multiplex it after all."""
    _posix(monkeypatch)
    host = RemoteHost(name="x", ssh_alias="alias")
    assert _control_options(host.ssh_argv(share_connection=False)) == ["ControlMaster=no", "ControlPath=none"]
    shared = _control_options(host.ssh_argv())
    assert shared[0] == "ControlMaster=auto" and shared[-1] == "ControlPersist=60s"


def test_on_windows_an_unshared_ssh_names_no_control_options(monkeypatch):
    monkeypatch.setattr("services.remote_config._is_windows", lambda: True)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: True)
    assert _control_options(RemoteHost(name="x", ssh_alias="alias").ssh_argv(share_connection=False)) == []


def test_the_batch_driver_stream_does_not_ride_the_shared_master(tmp_path, monkeypatch):
    """Through the real `_start_driver`, with a real `RemoteHost` building the argv it launches."""
    from tests.test_batch_driver import _batched, _FakeProc, _stub_runner

    _posix(monkeypatch)
    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    p._runner.host.ssh_argv = RemoteHost(name="x", ssh_alias="alias").ssh_argv
    spawned: list[list[str]] = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: spawned.append(list(argv)) or proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while not spawned and time.monotonic() < deadline:
        time.sleep(0.01)
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive()
    assert len(spawned) == 1
    assert "-tt" in spawned[0], "the stream keeps its PTY: Stop still reaches the remote through SIGHUP"
    assert _control_options(spawned[0]) == ["ControlMaster=no", "ControlPath=none"]


def test_the_single_shot_pipeline_stream_does_not_ride_the_shared_master(monkeypatch, tmp_path, stop_fetchers):
    from tests.test_remote_allocation_overlap import _spawn_recording_order

    _posix(monkeypatch)
    spawned: list[list[str]] = []
    _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers, "srun -N1", spawned=spawned)
    assert len(spawned) == 1
    assert _control_options(spawned[0]) == ["ControlMaster=no", "ControlPath=none"]


def test_a_missed_beat_is_logged_with_what_ssh_said():
    """The run log said only "(ssh exit 124)"; a timeout and a reset connection read the same."""
    said = "the remote did not answer within 30s: touch /r/hb"
    answers = [0, 124, 124]
    cache = types.SimpleNamespace(_ssh_run=lambda argv, *, check, **kw: types.SimpleNamespace(
        returncode=answers.pop(0), stdout="", stderr=f"\n{said}\n"))
    logged: list[str] = []
    hb = _UploadHeartbeat(cache, "/r/hb", log_fn=logged.append)
    for _ in range(3):
        hb.beat()
    assert logged == [f"[remote] WARNING the run's heartbeat did not reach the host 2 times in a row "
                      f"(ssh exit 124: {said}) — still trying\n"]
