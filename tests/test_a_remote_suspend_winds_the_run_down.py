"""A remote Suspend asks the pipeline to wind down; it does not kill the session.

``PipelineRunner.suspend`` used to do for a remote run exactly what Stop does:
signal the local ssh's process group and call ``RemoteRunner.cancel``. That
ends the ssh session the run's output travels over, stops the Fetcher's
per-well downloads, ``scancel``s a scheduler job, and SIGTERMs the login shell
— which is ``conda run`` or ``srun``, not the pipeline, so whether the pipeline
ever saw a polite request depended on software between the two. The wells in
flight were lost and the finished ones stranded on the remote.

A remote suspend is now a request file. The pipeline is launched with
``--suspend_file`` naming it and checks it wherever it checks the signal; a
batched driver checks it before starting another batch; and the app touches it
over a fresh ssh, leaving the run's stream and downloads alone.

These drive the pieces for real where that is possible: the flag through a
spawned worker process, the driver snippet through ``bash``, and the spawn and
the request against ``tests/_fake_remote.py``'s real filesystem.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._fake_remote import FakeRemote
from services.remote_fetch import FetchedOutputVerifier

_REPO = Path(__file__).resolve().parent.parent


# ── the pipeline half ────────────────────────────────────────────────────────

def test_the_request_file_suspends_the_process_and_the_workers_it_starts(tmp_path):
    """A worker is a separate process, and it has to see the request itself.

    The well workers check ``is_suspended()`` between FOV-groups in their own
    process, so a request only the orchestrator could see would leave every
    well in flight running to the end. Run in a subprocess because the flag is
    process-global and, once set, never clears.
    """
    request = tmp_path / ".aw_suspend_request"
    script = textwrap.dedent(f"""
        import multiprocessing as mp, sys
        sys.path.insert(0, {str(_REPO)!r})
        from pipeline import suspend

        def child(q):
            q.put(suspend.is_suspended())

        if __name__ == "__main__":
            suspend.watch_request_file({str(request)!r})
            print("before", suspend.is_suspended())
            open({str(request)!r}, "w").close()
            ctx = mp.get_context("spawn")
            q = ctx.Queue()
            p = ctx.Process(target=child, args=(q,))
            p.start(); p.join(60)
            print("worker", q.get(timeout=5))
            print("after", suspend.is_suspended())
    """)
    path = tmp_path / "probe.py"
    path.write_text(script)
    env = {k: v for k, v in os.environ.items() if k != "ALLWELL_SUSPEND_FILE"}
    out = subprocess.run([sys.executable, str(path)], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["before", "False", "worker", "True",
                                  "after", "True"], out.stdout


def test_the_pipeline_watches_the_file_it_is_given(monkeypatch, tmp_path):
    """``--suspend_file`` has to reach ``watch_request_file``, before any work."""
    import process_microscopy as pm

    seen: list[object] = []
    monkeypatch.setattr(pm._suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(pm._suspend, "watch_request_file",
                        lambda path, **k: seen.append(path))
    request = tmp_path / "req"
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(tmp_path / "missing"),
        "--output_dir", str(tmp_path / "out"), "--suspend_file", str(request)])
    with pytest.raises(SystemExit):
        pm.main()      # the missing input ends it — after the watch is armed
    assert seen == [request]


# ── the batched driver ───────────────────────────────────────────────────────

def _run_driver(tmp_path, *, suspend_during_batch_1: bool,
                timeout: float = 8) -> subprocess.CompletedProcess:
    """Run the real driver snippet in bash, with batch 1 ready and batch 2 not."""
    from services.remote_run_id import SUSPEND_REQUEST
    from services.remote_commands import (
        BATCH_READY,
        _batch_driver_snippet,
    )

    run_dir = tmp_path / "run"
    (run_dir / "b1").mkdir(parents=True)
    (run_dir / BATCH_READY.format(i=1)).touch()
    request = run_dir / SUSPEND_REQUEST
    # The "pipeline" for batch 1 is where a user's Suspend lands: it creates the
    # request, as `RemoteRunner.request_suspend` would, and exits 0 the way a
    # pipeline that wound down does.
    body = f"touch {request}" if suspend_during_batch_1 else "true"
    snippet = _batch_driver_snippet(str(run_dir), 3, ["bash", "-c", body],
                                    suspend_request=str(request))
    return subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=timeout)


def test_the_driver_starts_no_batch_after_a_suspend(tmp_path):
    out = _run_driver(tmp_path, suspend_during_batch_1=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "suspended — no further batches" in out.stdout
    assert "batch 2 starting" not in out.stdout


def test_without_a_request_the_driver_waits_for_the_next_batch(tmp_path):
    """The control: what the driver did on a suspend before — held the node.

    Two seconds is enough to tell: the suspended driver exits in well under
    one, and this one is sitting in a three-second poll for a batch that
    never arrives.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        _run_driver(tmp_path, suspend_during_batch_1=False, timeout=2)


# ── the spawn and the request, on a host with a filesystem ──────────────────

@pytest.fixture
def host():
    from services.remote_config import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


@pytest.fixture
def plate(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for name in ("A01.zip", "B02.zip"):
        with zipfile.ZipFile(in_dir / name, "w") as zf:
            zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"\0" * 256)
    (tmp_path / "out").mkdir()
    return tmp_path


@pytest.fixture
def spawned(host, plate, tmp_path, monkeypatch, stop_fetchers):
    """Spawn a real run against the fake host; return ``(runner, remote)``."""
    from services.remote_runner import (
        LocalInputMode,
        OutputFilter,
        RemoteRunner,
        ZippedInput,
    )
    from services.remote_upload import DatasetCache

    remote = FakeRemote(tmp_path / "remote-host")

    def _launched(_argv):
        m = MagicMock()
        m.stdout = MagicMock()
        m.stdout.__iter__ = lambda self: iter([])
        m.wait.return_value = 0
        m.returncode = 0
        m.pid = 4242
        return m

    remote.install(monkeypatch, host, on_launch=_launched)
    key = "cachekey" + "0" * 24
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, z: key)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, z, **k: (key, 0, 0.01))
    monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)
    monkeypatch.setattr(FetchedOutputVerifier, "_list_remote_completed_wells",
                        lambda self: set())

    def _spawn():
        in_dir = plate / "in"
        zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP,
                             well_zips=(in_dir / "A01.zip", in_dir / "B02.zip"))
        runner = RemoteRunner(host)
        stop_fetchers(runner)
        runner.spawn(zipped, plate / "out", pipeline_argv=[
            "/usr/bin/python", "/local/process_microscopy.py",
            "--input_dir", str(in_dir), "--output_dir", str(plate / "out"),
            "--nuclear_token", "DAPI"],
            output_filter=OutputFilter.FULL, log_fn=lambda _m: None)
        return runner

    return _spawn, remote


def _shell_home(text: str) -> str:
    return text.replace('"$HOME"/', "~/").replace("$HOME/", "~/")


def test_the_pipeline_is_launched_with_the_request_path(spawned):
    spawn, remote = spawned
    runner = spawn()
    request = runner._run.suspend_request
    assert request.endswith("/.aw_suspend_request"), request
    launched = _shell_home(" ".join(remote.launched[-1]))
    assert f"--suspend_file {request}" in launched, launched


def test_a_request_is_made_on_the_host_and_touches_nothing_else(spawned):
    spawn, remote = spawned
    runner = spawn()
    runner.cancel = MagicMock(side_effect=AssertionError("a suspend cancelled the run"))
    runner._fetcher = MagicMock()
    batched = MagicMock()
    log: list[str] = []

    assert runner.request_suspend(batched, log_fn=log.append) is True
    assert remote.exists(runner._run.suspend_request), remote.tree()
    batched.note_suspend_requested.assert_called_once_with()
    assert not runner._cancelled.is_set()
    runner._fetcher.cancel.assert_not_called()


def test_a_request_left_by_the_last_suspend_does_not_suspend_the_resume(spawned):
    """``run_id`` is deterministic, so a resume lands in the same directory."""
    spawn, remote = spawned
    first = spawn()
    assert first.request_suspend(log_fn=lambda _m: None)
    assert remote.exists(first._run.suspend_request)

    second = spawn()
    assert second._run.suspend_request == first._run.suspend_request
    assert not remote.exists(second._run.suspend_request), (
        "the previous run's suspend request survived into the resumed run, "
        "which would wind down before processing anything")


def _unreachable(monkeypatch, runner):
    monkeypatch.setattr(type(runner._cache), "_ssh_run",
                        lambda self, argv, **k: subprocess.CompletedProcess(
                            argv, 255, "", "Connection refused"))


def test_an_unreachable_host_asks_nothing_and_says_so(spawned, monkeypatch):
    """Single-shot: nothing is in effect, so the caller treats the run as going on."""
    spawn, _remote = spawned
    runner = spawn()
    _unreachable(monkeypatch, runner)
    log: list[str] = []
    assert runner.request_suspend(None, log_fn=log.append) is False
    assert "Connection refused" in "".join(log)
    assert "Press Suspend again" in "".join(log)


def test_a_batched_run_is_told_before_the_host_is_asked(spawned, monkeypatch):
    """Batched: told first, so the driver's early exit is never read as a lost
    node, and in effect even if the ``touch`` fails, because no more batches
    will be sent whatever the host says."""
    spawn, _remote = spawned
    runner = spawn()
    order: list[str] = []
    batched = MagicMock()
    batched.note_suspend_requested.side_effect = lambda: order.append("told")

    def ssh_run(self, argv, **k):
        order.append(argv[0])
        return subprocess.CompletedProcess(argv, 255, "", "Connection refused")

    monkeypatch.setattr(type(runner._cache), "_ssh_run", ssh_run)
    log: list[str] = []
    assert runner.request_suspend(batched, log_fn=log.append) is True
    assert order == ["told", "touch"]
    assert "no more will be sent" in "".join(log)


# ── PipelineRunner: which way a suspend goes ────────────────────────────────

class _Remote:
    def __init__(self, *, launched=True, reachable=True):
        self.can_request_suspend = launched
        self._reachable = reachable
        self.asked: list[object] = []
        self.cancelled = False

    def request_suspend(self, proc=None, *, log_fn=None):
        self.asked.append(proc)
        return self._reachable

    def cancel(self, _proc):
        self.cancelled = True


class _LiveProc:
    pid = 999_999

    def poll(self):
        return None


@pytest.fixture
def runner(monkeypatch):
    import services.pipeline_runner as pr

    signalled: list[object] = []
    monkeypatch.setattr(pr, "signal_subprocess",
                        lambda proc, **k: signalled.append(proc))
    r = pr.PipelineRunner()
    r._proc = _LiveProc()
    r._cleanup_remote = "run"
    r.signalled = signalled
    return r


def _settle(runner) -> None:
    """Wait for a remote suspend's request, which runs on a thread of its own."""
    if runner._suspend_thread is not None:
        runner._suspend_thread.join(5)


def test_a_launched_remote_run_is_asked_and_its_session_left_alone(runner):
    remote = _Remote()
    runner._remote_runner = remote
    runner.suspend()
    _settle(runner)
    assert remote.asked == [runner._proc]
    assert runner.signalled == [], "the local ssh was signalled, ending the session"
    assert not remote.cancelled, "the remote run was cancelled, not suspended"
    assert runner.was_suspended
    assert runner._cleanup_remote == "run", "the user's cleanup setting was changed"


def test_a_host_that_cannot_be_reached_leaves_the_run_as_it_was(runner):
    runner._remote_runner = _Remote(reachable=False)
    runner.suspend()
    _settle(runner)
    assert runner.signalled == []
    assert not runner.was_suspended, (
        "nothing was asked, so a run that goes on to finish must not be "
        "reported as suspended")
    assert runner._cleanup_remote == "run"


def test_a_remote_run_still_uploading_is_stopped_as_before(runner):
    remote = _Remote(launched=False)
    runner._remote_runner = remote
    runner.suspend()
    assert remote.asked == []
    assert runner.signalled, "a run with no pipeline yet must still stop"


def test_a_local_run_is_still_signalled(runner):
    runner.suspend()
    assert runner._proc in runner.signalled      # beside the (absent) zipper
    assert runner.was_suspended


# ── the batched wrapper ──────────────────────────────────────────────────────

def _batched(n_batches: int):
    import threading

    from services.remote_process import _BatchedRemoteProcess

    b = _BatchedRemoteProcess.__new__(_BatchedRemoteProcess)
    b._stop = threading.Event()
    b._suspending = threading.Event()
    b._stream_ended = threading.Event()
    b._driver_lost = threading.Event()
    b._batch_rc = {}
    b._batch_events = {}
    b._n_batches = n_batches
    b._line_q = __import__("queue").Queue()
    b._fetcher = MagicMock()
    b._log = lambda _m: None
    return b


def _driver_output(*lines: str):
    proc = MagicMock()
    proc.stdout = iter(lines)
    return proc


def test_a_driver_that_stops_for_a_suspend_is_not_a_lost_node():
    """Read as a lost node, the early exit would cancel the last batch's downloads."""
    from services.remote_commands import BATCH_DONE_MARKER

    b = _batched(3)
    b.note_suspend_requested()
    b._read_driver(_driver_output(
        BATCH_DONE_MARKER.format(i=1, rc=0) + "\n",
        "[remote] batch-driver: suspended — no further batches\n"))
    assert not b._driver_lost.is_set()
    assert not b._stop.is_set()
    b._fetcher.cancel.assert_not_called()


def test_a_driver_that_stops_without_a_suspend_is_still_a_lost_node():
    """The control: the lost-node rule itself is unchanged."""
    from services.remote_commands import BATCH_DONE_MARKER

    b = _batched(3)
    b._read_driver(_driver_output(BATCH_DONE_MARKER.format(i=1, rc=0) + "\n"))
    assert b._driver_lost.is_set()
    b._fetcher.cancel.assert_called_once_with()


# ── what the review of the first version found ──────────────────────────────

class _Ask:
    """A remote whose answers a test releases one at a time."""

    def __init__(self, answers):
        import threading

        self.can_request_suspend = True
        self._answers = list(answers)       # (release_event, result) per call
        self.lock = threading.Lock()

    def request_suspend(self, proc=None, *, log_fn=None):
        with self.lock:
            release, result = self._answers.pop(0)
        release.wait(5)
        return result


def test_a_slow_failed_click_does_not_undo_a_later_one_that_landed(runner):
    """Two clicks; the first ssh times out *after* the second succeeded.

    Before, the first thread cleared the suspend, so the run that the second
    request did wind down was reported as finished — success hooks and the
    half-done folder handed to Review.
    """
    import threading

    first, second = threading.Event(), threading.Event()
    runner._remote_runner = _Ask([(first, False), (second, True)])
    runner.suspend()
    t1 = runner._suspend_thread
    runner.suspend()
    t2 = runner._suspend_thread
    second.set()
    t2.join(5)
    first.set()
    t1.join(5)
    assert runner.was_suspended


def test_a_previous_run_s_late_thread_cannot_touch_the_next_run(runner):
    import threading

    gate = threading.Event()
    runner._remote_runner = _Ask([(gate, False)])
    runner.suspend()
    late = runner._suspend_thread
    runner._run_generation += 1          # what `start()` does for the next run
    runner._suspend_requested.set()      # the next run's own suspend
    runner._cleanup_remote = "none"
    gate.set()
    late.join(5)
    assert runner.was_suspended
    assert runner._cleanup_remote == "none"


def _drive_remote_run(monkeypatch, tmp_path, *, lines, suspend_flag, stop_flag=False):
    """Run the real `_run_pipeline_remote` with staging and the host stubbed."""
    import types

    import services.pipeline_runner as pr

    hooks: list[str] = []
    zipped = types.SimpleNamespace(
        up_to_date_wells=(), well_zips=(), mode=types.SimpleNamespace(value="zip"),
        on_pipeline_success=lambda: hooks.append("zipped"), cleanup=None)
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    verified: list[str] = []

    holder: list = []

    def _stream():
        for i, line in enumerate(lines):
            if stop_flag and i == 1:
                holder[0]._stop_requested.set()     # Stop, mid-run
            yield line

    class _Proc:
        pid = 1
        returncode = 0
        stdout = _stream()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    remote = types.SimpleNamespace(
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=lambda *a, **k: _Proc(),
        verify_and_cleanup=lambda ok, cleanup_mode, **k: verified.append(cleanup_mode) or True,
        close_remote_job=lambda **k: None,
        can_request_suspend=True)
    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = remote
    r._cleanup_remote = "all"
    holder.append(r)
    if suspend_flag:
        r._suspend_requested.set()
    opts = {"filename_schema": "a:b", "filename_sep": "_", "fluor_tokens": ["GFP"],
            "nuclear_token": "DAPI"}
    r._run_pipeline_remote(tmp_path / "in", tmp_path / "out", [], opts,
                           on_success=lambda: hooks.append("on_success"))
    kinds = []
    while not r.log_q.empty():
        kinds.append(r.log_q.get_nowait()[0])
    return r, kinds, verified, hooks


def test_a_run_that_says_it_saw_the_request_is_suspended_whatever_the_ssh_said(
        monkeypatch, tmp_path):
    """The ask failed (a timeout, say) and cleared the flag, but it had landed.

    The line is the pipeline's own, as `pipeline.suspend` writes it.
    """
    r, kinds, verified, hooks = _drive_remote_run(
        monkeypatch, tmp_path, suspend_flag=False,
        lines=["Well A01 F001 starting\n",
               "2026 WARNING SUSPEND requested (request file /x) — finishing\n",
               "Well A01 F001 done.\n"])
    assert "suspended" in kinds and "done" not in kinds, kinds
    # The configured cleanup, as for a finished run: it fires only once the
    # fetch is verified, and the resume reads nothing on the host (it uploads
    # only the missing groups and never re-attaches). Kept "for the resume",
    # the suspended run's directory stayed on the host for ever.
    assert verified == ["all"], "a suspended run's remote directory was kept for ever"
    assert hooks == []
    assert r.was_suspended


def test_a_run_that_never_saw_one_still_finishes(monkeypatch, tmp_path):
    """The control: no request on the stream and none asked is a completion."""
    r, kinds, verified, hooks = _drive_remote_run(
        monkeypatch, tmp_path, suspend_flag=False,
        lines=["Well A01 F001 starting\n", "Well A01 F001 done.\n"])
    assert "done" in kinds and "suspended" not in kinds, kinds
    assert verified == ["all"]
    assert sorted(hooks) == ["on_success", "zipped"]


def test_a_well_that_starts_after_the_request_leaves_nothing(tmp_path):
    """No CSV, no out.zip and no ``— done.`` line for a well that never began.

    The pool's call queue holds wells the orchestrator cannot cancel; each one
    used to run the whole end-of-well path with nothing in it and announce
    itself done, which a remote Fetcher then expected a CSV for.
    """
    import numpy as np
    tifffile = pytest.importorskip("tifffile")

    request = tmp_path / "req"
    request.touch()
    # A real well: one field, two channels, named by the default schema, so
    # the task gets as far as it would on a plate.
    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        for channel in ("DAPI", "GFP"):
            png = tmp_path / f"{channel}.tif"
            tifffile.imwrite(png, np.zeros((16, 16), dtype=np.uint16))
            zf.write(png, f"exp_{channel}_A01_F001_0h.tif")
    script = textwrap.dedent(f"""
        import sys, logging
        sys.path.insert(0, {str(_REPO)!r})
        logging.basicConfig(level=logging.INFO, stream=sys.stdout)
        import process_microscopy as pm
        pm._suspend.watch_request_file({str(request)!r})
        out = pm.process_well_zip_task(
            "A01", __import__("pathlib").Path({str(tmp_path / "A01.zip")!r}),
            __import__("pathlib").Path({str(tmp_path / "out")!r}), {{}},
            "DAPI", ["GFP"], "measurements")
        print("RESULT", out)
    """)
    path = tmp_path / "well.py"
    path.write_text(script)
    env = {k: v for k, v in os.environ.items() if k != "ALLWELL_SUSPEND_FILE"}
    out = subprocess.run([sys.executable, str(path)], capture_output=True,
                         text=True, timeout=120, env=env)
    text = out.stdout + out.stderr
    assert "RESULT ([], [])" in text, text[-2000:]
    assert "— done." not in text
    assert "starting" not in text
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_the_request_path_exists_only_once_the_upload_is_done(
        spawned, monkeypatch):
    """A suspend during the upload stops it; asked of the real spawn's ordering."""
    from services.remote_upload import DatasetCache

    spawn, _remote = spawned
    seen: list[bool] = []
    key = "cachekey" + "0" * 24

    def upload(self, z, **k):
        seen.append(bool(holder[0]._run and holder[0].can_request_suspend))
        return (key, 0, 0.01)

    holder: list = [None]
    monkeypatch.setattr(DatasetCache, "ensure_uploaded", upload)
    from services import remote_runner as rr
    real_init = rr.RemoteRunner.__init__

    def init(self, *a, **k):
        real_init(self, *a, **k)
        holder[0] = self

    monkeypatch.setattr(rr.RemoteRunner, "__init__", init)
    runner = spawn()
    assert seen == [False], "the run could be asked to suspend mid-upload"
    assert runner.can_request_suspend


def test_a_stopped_remote_run_that_exits_zero_is_neither_done_nor_cleaned(
        monkeypatch, tmp_path):
    """A Stop the remote pipeline wound down from is a stop: no success hooks,
    no "done", and its remote output left in place rather than deleted."""
    r, kinds, verified, hooks = _drive_remote_run(
        monkeypatch, tmp_path, suspend_flag=False, stop_flag=True,
        lines=["Well A01 F001 starting\n", "Well A01 F001 done.\n"])
    assert "done" not in kinds and "error" in kinds, kinds
    assert verified == ["none"]
    assert hooks == []
    assert r.cancel_state_at_failure == (True, False)


def test_a_well_suspended_before_its_first_group_leaves_nothing(tmp_path):
    """The task began, then the suspend landed before any group was attempted.

    That is the window between the task's own entry check and the group loop's
    first one — extracting the archive, finding its groups. The well ran the
    whole end-of-well path with nothing in it and printed ``— done.``, which a
    remote Fetcher reads as "a CSV to fetch": the suspended run then failed its
    verification instead of ending suspended.
    """
    import numpy as np
    tifffile = pytest.importorskip("tifffile")

    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        for channel in ("DAPI", "GFP"):
            png = tmp_path / f"{channel}.tif"
            tifffile.imwrite(png, np.zeros((16, 16), dtype=np.uint16))
            zf.write(png, f"exp_{channel}_A01_F001_0h.tif")
    script = textwrap.dedent(f"""
        import sys, logging, pathlib
        sys.path.insert(0, {str(_REPO)!r})
        logging.basicConfig(level=logging.INFO, stream=sys.stdout)
        import process_microscopy as pm
        real = pm.extract_zip
        def extract_then_suspend(*a, **k):
            n = real(*a, **k)
            pm._suspend.request_suspend()     # lands mid-task
            return n
        pm.extract_zip = extract_then_suspend
        out = pm.process_well_zip_task(
            "A01", pathlib.Path({str(tmp_path / "A01.zip")!r}),
            pathlib.Path({str(tmp_path / "out")!r}), {{}},
            "DAPI", ["GFP"], "measurements")
        print("RESULT", out)
    """)
    path = tmp_path / "well.py"
    path.write_text(script)
    env = {k: v for k, v in os.environ.items() if k != "ALLWELL_SUSPEND_FILE"}
    out = subprocess.run([sys.executable, str(path)], capture_output=True,
                         text=True, timeout=120, env=env)
    text = out.stdout + out.stderr
    assert "RESULT ([], [])" in text, text[-2000:]
    assert "— done." not in text, "a well that attempted nothing announced itself done"
    written = sorted(p.name for p in (tmp_path / "out").rglob("*") if p.is_file()) \
        if (tmp_path / "out").exists() else []
    assert not [n for n in written if n.endswith((".csv", "_out.zip"))], written


def test_a_second_local_suspend_sends_no_second_signal(runner):
    """The queue calls the runner directly, so the guard lives here too: a
    second SIGTERM is a hard stop once the pipeline's handler has fired."""
    runner.suspend()
    runner.suspend()
    assert runner.signalled.count(runner._proc) == 1
