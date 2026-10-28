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

import os
import pathlib
import subprocess
import time
from unittest.mock import MagicMock

import pytest

from services.remote_runner import (
    LocalInputMode,
    RemoteRunner,
    ZippedInput,
)
from services.remote_commands import _compose_remote_cmd, unpack_login_shell_command
from services.remote_config import RemoteHost
from services.remote_run_id import (
    UPLOAD_HEARTBEAT,
    UPLOAD_SENTINEL,
)
from services.remote_wait_snippets import UPLOAD_WAIT_TIMEOUT_S
from services.remote_upload import DatasetCache
from services.remote_fetch import FetchedOutputVerifier

_PIDFILE = "/scratch/runs/run-abc/run.pid"
_ARGV = ["python", "process_microscopy.py", "--input_dir", "/scratch/in"]
_SENTINEL = f"/scratch/runs/run-abc/{UPLOAD_SENTINEL}"


def test_a_scheduled_job_waits_for_the_upload_before_it_starts():
    cmd = _compose_remote_cmd(
        "srun -N1 -t 2:00:00", _PIDFILE, _ARGV,
        cache_hit=False, upload_sentinel=_SENTINEL,
    )
    assert "srun -t 2:00:00 --nodes=1 --ntasks=1" in cmd, "one task on one node (`remote_srun_retry.one_task`)"
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
    for fragment in ("echo $$ >", "srun --nodes=1 --ntasks=1", "SLURM_JOB_ID",
                     "process_microscopy.py"):
        assert fragment in without, fragment


@pytest.mark.parametrize("sentinel", ["", _SENTINEL])
def test_the_pipeline_is_always_the_last_thing_the_job_does(sentinel):
    """Whatever precedes it, the job ends by exec'ing the pipeline — so the
    allocation belongs to the pipeline rather than to a shell babysitting it."""
    cmd = _compose_remote_cmd("srun -N1", _PIDFILE, _ARGV, cache_hit=False,
                              upload_sentinel=sentinel)
    # The srun wrapper (services.remote_srun_retry) surrounds the launch with
    # its retry loop, so the job's script is the argument to `bash -lc`.
    inner = cmd.split(" bash -lc ", 1)[1].rsplit(' 2>&1; echo $? > "$_aw_rcf"', 1)[0]
    assert inner.rstrip().endswith("/scratch/in'")
    assert "exec python process_microscopy.py" in inner


# ── the order the two waits are actually spent in ────────────────────────────


def _host(launcher: str, **kw) -> RemoteHost:
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
        **kw,
    )


def _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers, launcher, *,
                           upload_raises=None, spawned=None, **host_kw):
    """Run ``spawn`` against fakes, returning the order of the outbound calls."""
    order: list[str] = []
    key = "cachekey" + "0" * 24

    def ssh_run(self, argv, *, check, **kw):
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
    monkeypatch.setattr(FetchedOutputVerifier, "_list_remote_completed_wells",
                        lambda self: set())
    cancelled: list[str] = []
    monkeypatch.setattr(RemoteRunner, "cancel",
                        lambda self, proc=None: cancelled.append("cancel"))

    def fake_popen(argv, **kw):
        order.append("allocate")
        if spawned is not None:
            spawned.append(list(argv))
        m = MagicMock()
        m.stdout = iter(())
        m.wait.return_value = 0
        m.returncode = 0
        return m

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    # ``subprocess.run`` is Popen underneath, so the fake above would answer the
    # sweep's ssh as though it were the pipeline launch. Give it its own stub:
    # an empty stdout is "no leftover job", which is the ordinary case.
    def fake_run(argv, **kw):
        order.append("sweep")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    zip_path = tmp_path / "A01.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                         well_zips=(zip_path,))
    runner = RemoteRunner(_host(launcher, **host_kw))
    stop_fetchers(runner)
    runner.spawn(zipped, tmp_path / "out",
                 ["python", "process_microscopy.py"],
                 output_filter=None, log_fn=lambda _m: None)
    return order, cancelled


def test_the_allocation_is_requested_before_the_upload_starts(monkeypatch, tmp_path, stop_fetchers):
    """The whole point: the queue wait and the transfer overlap. Asserted on
    the order of the outbound calls, because no value inside the runner records
    which of the two waits came first."""
    order, _ = _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers, "srun -N1")
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


def test_a_host_with_no_scheduler_still_uploads_first(monkeypatch, tmp_path, stop_fetchers):
    """There is nothing to queue for, and the pipeline starts the moment ssh
    connects — so starting it early would run it against an input directory
    that is still filling."""
    order, _ = _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers, "")
    assert order.index("upload") < order.index("allocate")


def test_a_failed_upload_hands_the_allocation_back(monkeypatch, tmp_path,
                                                  stop_fetchers):
    """Otherwise the node sits on the sentinel that will now never be written,
    until the timeout hours later."""
    boom = RuntimeError("rsync exited 23")
    with pytest.raises(RuntimeError):
        _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers, "srun -N1",
                               upload_raises=boom)


# ── surviving a crash ────────────────────────────────────────────────────────


def test_the_job_gives_up_on_a_dead_uploader_not_just_a_slow_one():
    """The deadline is for an uploader that is alive but wedged. The case that
    actually happens is the app being killed — force-quit, laptop closed,
    power gone — and waiting out two hours of idle, billed node for that is the
    wrong behaviour. A heartbeat that stops advancing frees the node in about a
    minute however long the upload legitimately needed."""
    from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S

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
    from services.remote_process import _UploadHeartbeat

    beats = []

    class _Cache:
        def _ssh_run(self, argv, *, check, **kw):
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

    monkeypatch.setattr("subprocess.run", fake_run)
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
    monkeypatch.setattr("subprocess.run",
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

    monkeypatch.setattr("subprocess.run", fake_run)
    runner._sweep_orphaned_allocation("/r/run.pid", "/r/.aw_upload_alive")

    from services.remote_run_state import _RemoteRun

    runner._run = _RemoteRun(
        run_id="run-x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/run.pid",
        local_output_dir=None, local_input_mode=LocalInputMode.ZIP,
        owns_remote_job=True,     # launched; a run that did not closes nothing
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
        "subprocess.run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, stdout="AW_NOTMINE 0 4242 alice\n", stderr=""),
    )
    runner._sweep_orphaned_allocation("/r/run.pid", "/r/.hb",
                                      log_fn=said.append)
    joined = "".join(said)
    assert "4242" in joined and "alice" in joined
    assert "not touching it" in joined


# ── the sweep's decision, run rather than read ───────────────────────────────
#
# The tests above assert the *shape* of the script. These run it under bash on
# six shapes of host, because both mistakes this guard can make are silent:
# cancelling a live job, and never cancelling a dead one.
#
# `stat -c %Y` is GNU coreutils, and a login node carrying only BSD's
# `stat -f %m` used to answer neither question. The two fallbacks disagreed
# about what an unreadable timestamp meant — the heartbeat's was "epoch 0", i.e.
# infinitely stale, and the sidecar's was "now", i.e. brand new — so such a host
# always landed on the sidecar's answer and no orphan was ever swept on it, for
# ever, with nothing in the log to say why. Now the answer is asked for in both
# spellings, and a genuinely unknowable one is reported as unknown and left
# alone rather than being given a default that happens to look decisive.

def _bin(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


_GNU_STAT = '#!/bin/bash\nexec /usr/bin/stat "$@"\n'
_BSD_STAT = ('#!/bin/bash\n'
             'if [ "$1" = "-c" ]; then exit 1; fi\n'
             'if [ "$1" = "-f" ]; then exec /usr/bin/stat -c %Y "$3"; fi\n'
             'exit 1\n')
_NO_STAT = '#!/bin/bash\nexit 1\n'


def _sweep_under_bash(monkeypatch, tmp_path, stat_shim, *, log=None, **host_kw):
    """Run the real sweep script through bash against a fabricated remote."""
    runner, pidfile, hb = _bash_host(monkeypatch, tmp_path, stat_shim, **host_kw)
    return runner._sweep_orphaned_allocation(
        pidfile, hb, log_fn=(log.append if log is not None else lambda _m: None))


def _bash_host(monkeypatch, tmp_path, stat_shim, *, heartbeat_age,
               sidecar_age, heartbeat_exists=True, owner=None,
               job_state="RUNNING", scancel_ends_it=True):
    """A runner whose ssh runs under local bash, against a fabricated remote.

    The stand-in ``squeue`` answers from a one-job table: *job_state* while the
    job is alive; ``None`` for a job that has ended and been purged, which real
    ``squeue -j`` answers with "Invalid job id" and exit 1; ``"DOWN"`` for a
    controller that cannot be reached. ``scancel`` ends the job and records its
    arguments in ``scancelled``.
    """
    me = subprocess.run(["id", "-un"], capture_output=True,
                        text=True).stdout.strip()
    pidfile = str(tmp_path / "run.pid")
    sidecar, hb = pidfile + ".slurm", str(tmp_path / ".aw_upload_alive")
    pathlib.Path(sidecar).write_text("991234\n")
    os.utime(sidecar, ((time.time() - sidecar_age),) * 2)
    if heartbeat_exists:
        pathlib.Path(hb).touch()
        os.utime(hb, ((time.time() - heartbeat_age),) * 2)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    table = tmp_path / "job_state"
    if job_state is not None:
        table.write_text(job_state)
    _bin(str(bindir), "stat", stat_shim)
    _bin(str(bindir), "scancel",
         f'#!/bin/bash\necho "$*" >> {tmp_path}/scancelled\n'
         + (f'rm -f {table}\n' if scancel_ends_it else ""))
    _bin(str(bindir), "squeue", (
        '#!/bin/bash\n'
        'fmt=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && fmt=$2; shift; done\n'
        f'[ -f {table} ] || {{ echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1; }}\n'
        f'st=$(cat {table})\n'
        'if [ "$st" = DOWN ]; then\n'
        '  echo "slurm_load_jobs error: Unable to contact slurm controller" >&2; exit 1; fi\n'
        f'if [ "$fmt" = "%u" ]; then echo "{owner or me}"; else echo "$st"; fi\n'))

    real_run = subprocess.run

    def fake_run(argv, **kw):
        kw.pop("timeout", None)
        return real_run(["bash", "-c", argv[-1]], **{
            **kw,
            "env": {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"},
            "timeout": 30,
        })

    monkeypatch.setattr("subprocess.run", fake_run)
    return RemoteRunner(_host("srun --time=1:00:00")), pidfile, hb


@pytest.mark.parametrize("shim,hb_age,sc_age,hb_exists,owner,expected", [
    # Beating now: somebody is there, whatever the sidecar says.
    (_GNU_STAT, 0, 9999, True, None, "owned"),
    # Silent for ten minutes and the sidecar is old too: nobody is coming back.
    (_GNU_STAT, 600, 9999, True, None, "swept"),
    # No heartbeat yet, but the job was submitted moments ago.
    (_GNU_STAT, 0, 10, False, None, "owned"),
    # Dead by every measure — and not ours.
    (_GNU_STAT, 600, 9999, True, "alice", "notmine"),
    # BSD-only `stat`: the age is readable, so the beat decides, same as GNU.
    (_BSD_STAT, 0, 9999, True, None, "owned"),
    # …and it can now reach a verdict a GNU-only reader never could.
    (_BSD_STAT, 600, 9999, True, None, "swept"),
    # No usable `stat` at all: unknowable, so left running.
    (_NO_STAT, 600, 9999, True, None, "unknown"),
])
def test_the_sweep_only_cancels_what_it_can_prove_is_dead(
        monkeypatch, tmp_path, shim, hb_age, sc_age, hb_exists, owner, expected):
    got = _sweep_under_bash(monkeypatch, tmp_path, shim,
                            heartbeat_age=hb_age, sidecar_age=sc_age,
                            heartbeat_exists=hb_exists, owner=owner)
    assert got == expected


# ── a job is somebody's only while the scheduler still runs it (review E4) ───
#
# A fresh heartbeat, or a fresh sidecar, used to be enough to call a directory
# "owned" — so Resume pressed right after Stop, or a retry right after a failed
# run, was refused with "another session is already running this exact
# dataset", naming a session that did not exist: the stopped run's last beat was
# under two minutes old, and its job had ended.

@pytest.mark.parametrize("job_state", [None, "COMPLETED", "CANCELLED by 1000", "FAILED"],
                         ids=["purged", "completed", "cancelled", "failed"])
@pytest.mark.parametrize("hb_age,sc_age,hb_exists", [(0, 9999, True), (0, 10, False)],
                         ids=["fresh-heartbeat", "fresh-sidecar"])
def test_a_job_that_has_ended_is_nobody_s_however_fresh_its_files(
        monkeypatch, tmp_path, job_state, hb_age, sc_age, hb_exists):
    said: list[str] = []
    got = _sweep_under_bash(monkeypatch, tmp_path, _GNU_STAT, heartbeat_age=hb_age,
                            sidecar_age=sc_age, heartbeat_exists=hb_exists,
                            job_state=job_state, log=said)
    assert got == "none", said
    assert "another session" not in "".join(said)
    assert not (tmp_path / "scancelled").exists(), "an ended job was cancelled"
    assert not (tmp_path / "run.pid.slurm").exists(), (
        "the record of a job that has ended was kept, so every later run asks about it again")


def test_a_scheduler_that_cannot_be_asked_proves_nothing(monkeypatch, tmp_path):
    said: list[str] = []
    got = _sweep_under_bash(monkeypatch, tmp_path, _GNU_STAT, heartbeat_age=600,
                            sidecar_age=9999, job_state="DOWN", log=said)
    assert got == "unknown", said
    assert not (tmp_path / "scancelled").exists()
    assert (tmp_path / "run.pid.slurm").read_text().strip() == "991234"
    assert "squeue could not say whether job 991234" in "".join(said)


def test_a_swept_job_is_forgotten_once_the_scheduler_lets_it_go(monkeypatch, tmp_path):
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    got = _sweep_under_bash(monkeypatch, tmp_path, _GNU_STAT, heartbeat_age=600,
                            sidecar_age=9999)
    assert got == "swept"
    assert (tmp_path / "scancelled").read_text().split("\n")[0] == f"-u {me} 991234"
    assert not (tmp_path / "run.pid.slurm").exists()
    assert not (tmp_path / ".aw_upload_alive").exists()


def test_a_cancelled_job_still_shutting_down_still_holds_the_directory(monkeypatch, tmp_path):
    """The caller is about to write where the cancelled pipeline wrote, and a
    pipeline answers SIGTERM by winding down — for up to Slurm's KillWait."""
    from services import remote_sweep

    monkeypatch.setattr(remote_sweep, "CANCEL_SETTLE_S", 2)
    said: list[str] = []
    got = _sweep_under_bash(monkeypatch, tmp_path, _GNU_STAT, heartbeat_age=600,
                            sidecar_age=9999, scancel_ends_it=False, log=said)
    assert got == "owned", said
    assert "still shutting down 2 s later" in "".join(said)
    assert (tmp_path / "run.pid.slurm").exists()


def test_resume_right_after_stop_is_not_refused(monkeypatch, tmp_path):
    """The app's own order: Stop's teardown cancels the run's job, then Resume
    sweeps the same directory seconds later — its heartbeat under a minute old."""
    from services.remote_run_state import _RemoteRun

    runner, pidfile, hb = _bash_host(monkeypatch, tmp_path, _GNU_STAT,
                                     heartbeat_age=5, sidecar_age=9999)
    runner._run = _RemoteRun(
        run_id="run-x", cache_key="k", remote_input_dir=str(tmp_path / "in"),
        remote_output_dir=str(tmp_path / "out"), remote_pidfile=pidfile,
        local_output_dir=None, local_input_mode=LocalInputMode.ZIP,
        owns_remote_job=True)
    said: list[str] = []
    runner.close_remote_job(log_fn=said.append)
    assert "cancelled lingering job 991234" in "".join(said), said

    resume = RemoteRunner(runner.host)
    got = resume._sweep_orphaned_allocation(pidfile, hb, log_fn=said.append)
    assert got == "none", "".join(said)
    assert "another session" not in "".join(said)


@pytest.mark.parametrize("exclusive, expected", [
    (True, "srun --exclusive --partition=gpu --time=1:00:00"),
    (False, "srun --partition=gpu --time=1:00:00"),
])
def test_a_scheduled_run_asks_for_the_node_to_itself(
        monkeypatch, tmp_path, stop_fetchers, exclusive, expected):
    """The single-launch path sends ``RemoteHost.launch_command``: with
    ``exclusive_node`` on (the default) srun carries ``--exclusive``, so a
    second run — another app instance's — cannot be placed on the same machine;
    off, the command is exactly what the user wrote
    (``tests/test_a_run_reserves_its_node.py`` covers the other launch sites)."""
    spawned: list[list[str]] = []
    _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers,
                           "srun --partition=gpu --time=1:00:00",
                           spawned=spawned, exclusive_node=exclusive)
    assert len(spawned) == 1
    script = unpack_login_shell_command(spawned[0][-1])
    assert expected in script
    assert ("--exclusive" in script) is exclusive
