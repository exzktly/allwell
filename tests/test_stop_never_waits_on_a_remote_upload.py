"""Stop, pressed while a remote run is still uploading, answers at once.

Reported as "clicking Stop crashes the app" on a remote run. Measured in the
full app window: Stop during the upload froze the window for the whole of the
remaining upload — ``_spawn_tracked`` holds ``_proc_lock`` around the entire
``RemoteRunner.spawn`` (staging, upload, launch) and ``stop`` took that lock on
the GUI thread — and the upload never heard the Stop, because the runner did
not pass ``spawn`` the stop event it accepts. When the upload finally ended the
pipeline was launched, killed, and reported as a failure. On a real plate that
is minutes to hours of a window that does not respond.

These drive the real ``PipelineRunner`` on its own thread, with a remote whose
``spawn`` uploads the way ``_stream_rsync_with_retry`` does: until done, or
until the stop event it was given is set, whereupon rsync fails.
"""

from __future__ import annotations

import threading
import time
import types

UPLOAD_S = 20.0


def _runner(monkeypatch, tmp_path, *, launches: list, cancels: list):
    import services.pipeline_runner as pr

    zipped = types.SimpleNamespace(
        up_to_date_wells=(), well_zips=(), incremental=False, mode=types.SimpleNamespace(value="zip"),
        on_pipeline_success=None, cleanup=None)
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    uploading = threading.Event()

    class _Proc:
        pid = 1
        returncode = -15
        stdout = iter(())
        _ssh = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    def spawn(*a, stop_event=None, **k):
        uploading.set()
        end = time.monotonic() + UPLOAD_S
        while time.monotonic() < end:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("rsync upload of well zips to h failed (rc=20)")
            time.sleep(0.02)
        launches.append(True)
        return _Proc()

    remote = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=spawn,
        verify_and_cleanup=lambda ok, **k: True,
        close_remote_job=lambda **k: None,
        cancel=lambda proc: cancels.append(proc),
        can_request_suspend=False)
    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = remote
    opts = {"filename_schema": "a:b", "filename_sep": "_", "fluor_tokens": ["GFP"],
            "nuclear_token": "DAPI"}
    th = threading.Thread(target=r._run_pipeline_remote, daemon=True,
                          args=(tmp_path / "in", tmp_path / "out", [], opts))
    th.start()
    assert uploading.wait(10), "the run never reached its upload"
    return r, th


def _events(r):
    out = []
    while not r.log_q.empty():
        out.append(r.log_q.get_nowait())
    return out


def test_stop_during_the_upload_returns_at_once_and_ends_it(monkeypatch, tmp_path):
    launches: list = []
    r, th = _runner(monkeypatch, tmp_path, launches=launches, cancels=[])
    t0 = time.monotonic()
    r.stop()
    took = time.monotonic() - t0
    assert took < 1.0, f"Stop blocked the GUI thread for {took:.1f}s"
    th.join(10)
    assert not th.is_alive(), "the upload went on after Stop"
    assert launches == [], "the pipeline was launched after Stop"
    events = _events(r)
    errors = [p for k, p in events if k == "error"]
    assert errors and "stopped" in errors[0] and "Failed to run" not in errors[0], errors
    assert r.cancel_state_at_failure[0] is True


def test_a_launch_that_raced_the_stop_is_signalled_by_the_spawner(monkeypatch, tmp_path):
    """The other half: a spawn that finishes just as Stop lands has launched a
    pipeline ``stop`` could not see. ``_spawn_tracked`` signals it itself."""
    import services.pipeline_runner as pr

    signalled: list = []
    monkeypatch.setattr(pr.PipelineRunner, "_signal_subprocess",
                        staticmethod(lambda p, **k: signalled.append(p)))
    r = pr.PipelineRunner()
    launched = object()

    def _spawn():
        r._stop_requested.set()          # Stop lands mid-spawn
        return launched

    assert r._spawn_tracked(_spawn) is launched
    assert signalled == [launched]


def test_the_remote_kill_does_not_run_on_the_gui_thread(monkeypatch, tmp_path):
    """Two ssh round trips (the kill, then scancel) used to run inside the Stop
    button's slot, freezing the window for as long as the host took."""
    import services.pipeline_runner as pr

    monkeypatch.setattr(pr.PipelineRunner, "_signal_subprocess",
                        staticmethod(lambda p, **k: None))
    gate, ran_on = threading.Event(), []

    def _slow_cancel(proc):
        ran_on.append(threading.current_thread().name)
        gate.wait(10)

    r = pr.PipelineRunner()
    r._remote_runner = types.SimpleNamespace(cancel=_slow_cancel)
    r._proc = types.SimpleNamespace(pid=1, poll=lambda: None)
    t0 = time.monotonic()
    r.stop()
    took = time.monotonic() - t0
    gate.set()
    assert took < 1.0, f"Stop waited {took:.1f}s on the remote kill"
    deadline = time.monotonic() + 5
    while not ran_on and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ran_on and ran_on[0] != threading.current_thread().name

