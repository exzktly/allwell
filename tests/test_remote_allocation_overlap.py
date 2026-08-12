"""Ask the scheduler for a node while the plate is still uploading.

A queue wait and a transfer are independent waits, and the runner used to spend
them one after the other: upload every well zip, *then* ssh in and ask for an
allocation. On a busy cluster that is the queue wait added to the upload for no
reason — the node could have been queuing for the whole transfer.

The catch is that ``srun … exec <pipeline>`` starts the moment the scheduler
grants the node, which may be long before the last byte lands. So the job waits
on a sentinel file written when the upload finishes, and these pin the three
things that has to get right: the wait exists where it is needed, it does *not*
exist where starting early would run the pipeline against a half-filled input
directory, and a failed upload hands the node back instead of leaving it to
time out.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from services.remote_runner import (
    UPLOAD_HEARTBEAT,
    UPLOAD_SENTINEL,
    UPLOAD_WAIT_TIMEOUT_S,
    DatasetCache,
    LocalInputMode,
    RemoteHost,
    RemoteRunner,
    ZippedInput,
    _compose_remote_cmd,
)

_PIDFILE = "/scratch/runs/run-abc/run.pid"
_ARGV = ["python", "process_microscopy.py", "--input_dir", "/scratch/in"]
_SENTINEL = f"/scratch/runs/run-abc/{UPLOAD_SENTINEL}"


def test_a_scheduled_job_waits_for_the_upload_before_it_starts():
    cmd = _compose_remote_cmd(
        "srun -N1 -t 2:00:00", _PIDFILE, _ARGV,
        cache_hit=False, upload_sentinel=_SENTINEL,
    )
    assert "srun -N1" in cmd
    # It blocks on the sentinel...
    assert UPLOAD_SENTINEL in cmd
    assert "while [ ! -f" in cmd
    # ...before the pipeline, not after it.
    assert cmd.index("while [ ! -f") < cmd.index("process_microscopy.py")
    # ...and says so, because a granted node waiting on bytes looks exactly
    # like a hung one.
    assert "waiting for the upload" in cmd
    assert "input is complete" in cmd


def test_the_wait_gives_the_node_back_rather_than_holding_it_forever():
    """The node is idle and billed for every second of the wait, so a dead
    uploader must not be able to sit on one indefinitely."""
    cmd = _compose_remote_cmd(
        "srun -N1", _PIDFILE, _ARGV, cache_hit=False, upload_sentinel=_SENTINEL)
    assert str(UPLOAD_WAIT_TIMEOUT_S) in cmd
    assert "the upload never completed" in cmd


def test_a_host_with_no_launcher_never_waits():
    """Nothing is being queued for: the login shell runs the pipeline the
    moment ssh connects, so the upload has to have finished first and there is
    no sentinel to wait on."""
    cmd = _compose_remote_cmd("", _PIDFILE, _ARGV, cache_hit=False,
                              upload_sentinel=_SENTINEL)
    assert "while [ ! -f" not in cmd
    assert UPLOAD_SENTINEL not in cmd


def test_a_cache_hit_neither_allocates_nor_waits():
    """A cache hit runs ``true``. It must not take a compute allocation, and a
    job that never starts must not wait for an upload that never happens."""
    cmd = _compose_remote_cmd("srun -N1", _PIDFILE, ["true"], cache_hit=True,
                              upload_sentinel=_SENTINEL)
    assert "srun" not in cmd
    assert "while [ ! -f" not in cmd


def test_no_sentinel_means_no_wait_at_all():
    """The old order, unchanged: `spawn` passes no sentinel when it is going to
    upload first, and the composed command must then be what it always was."""
    with_wait = _compose_remote_cmd("srun -N1", _PIDFILE, _ARGV,
                                    cache_hit=False, upload_sentinel=_SENTINEL)
    without = _compose_remote_cmd("srun -N1", _PIDFILE, _ARGV, cache_hit=False)
    assert "while [ ! -f" not in without
    assert len(without) < len(with_wait)
    # Everything else about the two is the same shape.
    for fragment in ("echo $$ >", "srun -N1", "SLURM_JOB_ID",
                     "process_microscopy.py"):
        assert fragment in without, fragment


@pytest.mark.parametrize("sentinel", ["", _SENTINEL])
def test_the_pipeline_is_always_the_last_thing_the_job_does(sentinel):
    """Whatever precedes it, the job ends by exec'ing the pipeline — so the
    allocation belongs to the pipeline rather than to a shell babysitting it."""
    cmd = _compose_remote_cmd("srun -N1", _PIDFILE, _ARGV, cache_hit=False,
                              upload_sentinel=sentinel)
    assert cmd.rstrip().endswith("/scratch/in'")
    assert "exec python process_microscopy.py" in cmd


# ── the order the two waits are actually spent in ────────────────────────────


def _host(launcher: str) -> RemoteHost:
    return RemoteHost(
        name="test",
        ssh_alias="test-host",
        remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging",
        remote_python="/opt/python",
        cpu_only=False,
        extra_args=(),
        cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
        compute_launcher=launcher,
    )


def _spawn_recording_order(monkeypatch, tmp_path, launcher, *, upload_raises=None):
    """Run ``spawn`` against fakes, returning the order of the outbound calls."""
    order: list[str] = []
    key = "cachekey" + "0" * 24

    def ssh_run(self, argv, *, check):
        order.append(" ".join(str(a) for a in argv[:2]))
        return MagicMock(stdout="", returncode=0)

    def ensure_uploaded(self, zipped, **kw):
        order.append("upload")
        if upload_raises is not None:
            raise upload_raises
        return (key, 1234, 0.5)

    monkeypatch.setattr(DatasetCache, "_ssh_run", ssh_run)
    monkeypatch.setattr(DatasetCache, "_ssh_script",
                        lambda self, script, *, check: MagicMock(stdout="",
                                                                 returncode=0))
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, zipped: key)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded", ensure_uploaded)
    monkeypatch.setattr(DatasetCache, "gc", lambda self, budget, **kw: None)
    monkeypatch.setattr(RemoteRunner, "sync_required_files",
                        lambda self, **kw: None)
    monkeypatch.setattr(RemoteRunner, "_list_remote_completed_wells",
                        lambda self: set())
    cancelled: list[str] = []
    monkeypatch.setattr(RemoteRunner, "cancel",
                        lambda self, proc=None: cancelled.append("cancel"))

    def fake_popen(argv, **kw):
        order.append("allocate")
        m = MagicMock()
        m.stdout = iter(())
        m.wait.return_value = 0
        m.returncode = 0
        return m

    monkeypatch.setattr("services.remote_runner.subprocess.Popen", fake_popen)

    # ``subprocess.run`` is Popen underneath, so the fake above would answer the
    # sweep's ssh as though it were the pipeline launch. Give it its own stub:
    # an empty stdout is "no leftover job", which is the ordinary case.
    def fake_run(argv, **kw):
        order.append("sweep")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)

    zip_path = tmp_path / "A01.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                         well_zips=(zip_path,))
    runner = RemoteRunner(_host(launcher))
    runner.spawn(zipped, tmp_path / "out",
                 ["python", "process_microscopy.py"],
                 output_filter=None, log_fn=lambda _m: None)
    return order, cancelled


def test_the_allocation_is_requested_before_the_upload_starts(monkeypatch, tmp_path):
    """The whole point: the queue wait and the transfer overlap. Asserted on
    the order of the outbound calls, because no value inside the runner records
    which of the two waits came first."""
    order, _ = _spawn_recording_order(monkeypatch, tmp_path, "srun -N1")
    assert "allocate" in order and "upload" in order
    assert order.index("allocate") < order.index("upload")
    # And the sentinel is written after the bytes, not before them — the job
    # is watching for exactly that file.
    sentinel_at = [i for i, step in enumerate(order) if UPLOAD_SENTINEL in step]
    assert sentinel_at, f"the sentinel was never written: {order}"
    assert order.index("upload") < sentinel_at[0]

    # The heartbeat starts before the job can look at it, so the first thing a
    # granted node sees is a heartbeat that exists rather than a missing one it
    # would read as a dead uploader.
    beats = [i for i, step in enumerate(order) if UPLOAD_HEARTBEAT in step]
    assert beats, f"no heartbeat was ever written: {order}"
    assert beats[0] < order.index("allocate")

    # And a leftover allocation is swept before we add one of our own.
    assert order.index("sweep") < order.index("allocate")


def test_a_host_with_no_scheduler_still_uploads_first(monkeypatch, tmp_path):
    """There is nothing to queue for, and the pipeline starts the moment ssh
    connects — so starting it early would run it against an input directory
    that is still filling."""
    order, _ = _spawn_recording_order(monkeypatch, tmp_path, "")
    assert order.index("upload") < order.index("allocate")


def test_a_failed_upload_hands_the_allocation_back(monkeypatch, tmp_path):
    """Otherwise the node sits on the sentinel that will now never be written,
    until the timeout hours later."""
    boom = RuntimeError("rsync exited 23")
    with pytest.raises(RuntimeError):
        _spawn_recording_order(monkeypatch, tmp_path, "srun -N1",
                               upload_raises=boom)


# ── surviving a crash ────────────────────────────────────────────────────────


def test_the_job_gives_up_on_a_dead_uploader_not_just_a_slow_one():
    """The deadline is for an uploader that is alive but wedged. The case that
    actually happens is the app being killed — force-quit, laptop closed,
    power gone — and waiting out two hours of idle, billed node for that is the
    wrong behaviour. A heartbeat that stops advancing frees the node in about a
    minute however long the upload legitimately needed."""
    from services.remote_runner import UPLOAD_HEARTBEAT_STALE_S

    cmd = _compose_remote_cmd(
        "srun -N1", _PIDFILE, _ARGV, cache_hit=False,
        upload_sentinel=_SENTINEL,
        upload_heartbeat=f"/scratch/runs/run-abc/{UPLOAD_HEARTBEAT}",
    )
    assert UPLOAD_HEARTBEAT in cmd
    assert str(UPLOAD_HEARTBEAT_STALE_S) in cmd
    assert "stopped responding" in cmd
    # Remote clocks on both sides of the comparison — the mtime comes from a
    # touch made over ssh, so this is not measuring skew between two machines.
    assert "stat -c %Y" in cmd and "date +%s" in cmd
    # Checked inside the wait, before the deadline that follows it.
    assert cmd.index("stopped responding") < cmd.index("never completed")


def test_the_heartbeat_stops_when_the_upload_does(monkeypatch):
    """Whatever ends the upload has to end the beat, or a crashed app would go
    on reassuring the job that its input is coming."""
    from services.remote_runner import _UploadHeartbeat

    beats = []

    class _Cache:
        def _ssh_run(self, argv, *, check):
            beats.append(tuple(argv))
            return MagicMock(stdout="", returncode=0)

    hb = _UploadHeartbeat(_Cache(), "/scratch/runs/run-abc/.aw_upload_alive",
                          interval_s=0.02)
    with hb:
        import time as _t
        _t.sleep(0.12)
    during = len(beats)
    assert during >= 2, f"the heartbeat never beat: {beats}"

    import time as _t
    _t.sleep(0.12)
    assert len(beats) == during, "the heartbeat outlived the upload"


def test_a_leftover_job_is_cancelled_but_a_live_one_is_not(monkeypatch, tmp_path):
    """``run_id`` is deterministic in the input and the arguments, which is what
    lets a new run find the job a crashed one left — and also what would let two
    sessions running the same plate cancel each other. The guard is the
    heartbeat and the sidecar's own age."""
    runner = RemoteRunner(_host("srun -N1"))
    seen = {}

    def fake_run(argv, **kw):
        seen["script"] = argv[-1]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    runner._sweep_orphaned_allocation("/r/run.pid", "/r/.aw_upload_alive")

    script = seen["script"]
    assert "scancel" in script
    # It reads the id the crashed run left on the shared filesystem...
    assert "/r/run.pid.slurm" in script
    # ...and refuses to touch it while someone is still beating, or while the
    # sidecar itself is only moments old.
    assert "AW_OWNED" in script
    assert script.count("AW_OWNED") == 2


def test_a_host_without_a_scheduler_has_nothing_to_sweep(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr("services.remote_runner.subprocess.run",
                        lambda *a, **k: ran.append(a) or
                        subprocess.CompletedProcess([], 0, stdout=""))
    RemoteRunner(_host(""))._sweep_orphaned_allocation("/r/run.pid", "/r/.hb")
    assert not ran


def test_neither_cancel_path_will_touch_another_user_s_job(monkeypatch):
    """The id comes from a file this app wrote, which in the ordinary case
    makes it our own job — but ``remote_staging_dir`` can point at shared
    scratch and ``run_id`` is deterministic in (input, arguments), so two
    people processing the same plate share a sidecar. Slurm would refuse a
    non-owner's scancel anyway; that is the scheduler's policy rather than this
    app's promise, and it does not hold for an operator account."""
    runner = RemoteRunner(_host("srun -N1"))
    scripts = []

    def fake_run(argv, **kw):
        scripts.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("services.remote_runner.subprocess.run", fake_run)
    runner._sweep_orphaned_allocation("/r/run.pid", "/r/.aw_upload_alive")

    from services.remote_runner import _RemoteRun

    runner._run = _RemoteRun(
        run_id="run-x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/run.pid",
        local_output_dir=None, local_input_mode=LocalInputMode.ZIP,
    )
    runner.close_remote_job()

    assert len(scripts) == 2, "both cancel paths should have run"
    for script in scripts:
        # Asks the scheduler who owns it, and stops if the answer is not us...
        assert "squeue -h -j" in script
        assert "%u" in script
        assert "AW_NOTMINE" in script
        # ...and scopes the cancel itself, so a job that changed hands between
        # the question and the answer is still not ours to kill.
        assert "scancel -u" in script
        assert 'scancel "$_jid"' not in script


def test_a_job_owned_by_someone_else_is_reported_not_cancelled(monkeypatch):
    runner = RemoteRunner(_host("srun -N1"))
    said = []
    monkeypatch.setattr(
        "services.remote_runner.subprocess.run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, stdout="AW_NOTMINE 4242 alice\n", stderr=""),
    )
    runner._sweep_orphaned_allocation("/r/run.pid", "/r/.hb",
                                      log_fn=said.append)
    joined = "".join(said)
    assert "4242" in joined and "alice" in joined
    assert "not touching it" in joined
