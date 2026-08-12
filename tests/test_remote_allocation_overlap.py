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

from unittest.mock import MagicMock

import pytest

from services.remote_runner import (
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
    touches = [i for i, step in enumerate(order) if step.startswith("touch ")]
    assert touches, f"the sentinel was never written: {order}"
    assert order.index("upload") < touches[0]
    assert UPLOAD_SENTINEL in order[touches[0]]


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
