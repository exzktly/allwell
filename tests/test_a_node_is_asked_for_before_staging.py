"""A scheduled run asks for its node before the plate is staged, and hands the node the run once it is ready.

Requested: the app used to ask Slurm for the node only after staging had planned, filtered and rewritten every
archive, so on a large plate the queue wait began after minutes of local work it could have overlapped. Now a job is
queued as soon as staging knows how many wells the run can hold — before ``[remote] planning N well archive(s)…`` —
and waits for a command file the app writes once the run is decided (``services.remote_reservation``).

These run the job's shell for real: the launch the app composes is unpacked and executed by ``bash`` against
``tests/_fake_remote.py``'s host, with a plain launcher (``env``) standing in for the scheduler, so the wait, the
hand-over, the pidfile links and the stream the run reads are what the host and the run actually saw.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from services.remote_commands import _compose_reservation_cmd
from services.remote_reservation import (
    _release_script,
    _write_command_script,
    batches_upper_bound,
    reservation_paths,
)
from tests._fake_remote import FakeRemote

SCHEMA = "experiment:channel:well:fov:timepoint"
WELLS = ("A01", "B02")
SOURCED = "[test] the job sourced the command file it was handed"


# ── the walltime a batched job is asked for ────────────────────────────────────

@pytest.mark.parametrize(("n_wells", "batch_size", "nanowell", "expected"), [
    (10, 0, False, 0),       # single-shot: the launcher's own --time
    (10, 0, True, 0),
    (10, 4, False, 3),       # 4 + 4 + 2
    (8, 4, False, 2),
    (8, 4, True, 3),         # the re-anchoring pass is one more batch
    (0, 4, False, 1),
])
def test_a_batched_job_is_sized_for_every_batch_the_plate_can_hold(n_wells, batch_size, nanowell, expected):
    assert batches_upper_bound(n_wells, batch_size, nanowell=nanowell) == expected


# ── the job's shell, run by bash ───────────────────────────────────────────────

def _job(tmp_path: Path, *, heartbeat_age: float = 0.0):
    host = MagicMock(remote_staging_dir=str(tmp_path))
    paths = reservation_paths(host, "tok")
    os.makedirs(paths.dir)
    Path(paths.heartbeat).touch()
    if heartbeat_age:
        os.utime(paths.heartbeat, (time.time() - heartbeat_age,) * 2)
    script = _compose_reservation_cmd("env", paths.pidfile, paths.command, paths.heartbeat)
    proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return paths, proc


def _read_until(proc, text: str, timeout: float = 30.0) -> list[str]:
    """The job's lines up to and including the first containing *text*; fails if it exits or *timeout* passes first.

    A reader thread, because ``readline`` on the pipe blocks and a job that never prints *text* would hang the test."""
    import queue
    import threading

    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(ln) for ln in proc.stdout] + [lines.put(None)], daemon=True).start()
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        assert line is not None, f"the job exited before saying {text!r}: {''.join(seen)}"
        seen.append(line)
        if text in line:
            proc._aw_lines = lines                 # the rest of its output, for the caller
            return seen
    raise AssertionError(f"the job had not said {text!r} after {timeout:.0f} s: {''.join(seen)}")


def _rest(proc, timeout: float = 30.0) -> list[str]:
    """The job's remaining lines, once it has exited."""
    proc.wait(timeout=timeout)
    rest: list[str] = []
    while (line := proc._aw_lines.get(timeout=5)) is not None:
        rest.append(line)
    return rest


def test_the_job_waits_for_the_run_and_then_runs_it_in_its_own_shell(tmp_path):
    paths, proc = _job(tmp_path)
    try:
        # The command file is written only once the job has said it is waiting for it. A fixed one-second sleep
        # here let a slow CI runner reach its first check after the file existed, so the job, correctly, never
        # said it was waiting, and the assertion below failed on a job that had behaved exactly as it should.
        head = _read_until(proc, "waiting for the app to finish preparing the run")
        assert proc.poll() is None, "the job did not wait for the run"
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "heartbeat").touch()
        run_pid = str(run_dir / "run.pid")
        body = f'echo "RAN pid=$$ recorded=$(cat {run_pid})"; exit 7\n'
        subprocess.run(["sh", "-c", _write_command_script(paths, body, run_pid, str(run_dir / "heartbeat"))],
                       check=True)
        out = "".join(head + _rest(proc))
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 7, out
    lines = out.splitlines()
    ran = [ln for ln in lines if ln.startswith("RAN ")]
    assert len(ran) == 1, out
    # The run directory's pidfile names the job's own shell: Stop's kill reaches the job through it.
    shell_pid, recorded = (part.split("=")[1] for part in ran[0][4:].split())
    assert shell_pid == recorded, out
    assert os.readlink(run_pid) == paths.pidfile
    assert os.readlink(run_pid + ".slurm") == paths.pidfile + ".slurm"
    assert os.readlink(paths.heartbeat) == str(run_dir / "heartbeat")


def test_a_job_whose_app_went_quiet_gives_the_node_back_without_a_run(tmp_path):
    paths, proc = _job(tmp_path, heartbeat_age=3600)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 75, out
    assert "the app stopped responding while it prepared the run" in out, out
    assert not os.path.exists(paths.command)


def test_giving_back_an_unused_job_cancels_it_and_removes_its_directory(tmp_path):
    host = MagicMock(remote_staging_dir=str(tmp_path))
    paths = reservation_paths(host, "tok")
    os.makedirs(paths.dir)
    Path(paths.pidfile + ".slurm").write_text("9191\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "scancel").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/scancelled\n')
    (bindir / "scancel").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    subprocess.run(["sh", "-c", _release_script(paths)], check=True, env=env)
    assert (tmp_path / "scancelled").read_text().split() == ["9191"]
    assert not os.path.exists(paths.dir)


# ── against a host that runs what it is sent ───────────────────────────────────

@pytest.fixture
def host():
    from services.remote_config import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10, measured_bandwidth_mbps=100.0,
        # Not srun: a launcher the test machine has, so the job's own shell runs here for real.
        compute_launcher="env", exclusive_node=False,
    )


class _Jobs:
    """``on_launch`` for the fake host: every launch runs, for real, in ``bash`` on the host."""

    def __init__(self, remote: FakeRemote) -> None:
        self.remote = remote
        self.procs: list[subprocess.Popen] = []

    def __call__(self, argv):
        proc = FakeRemote._REAL_POPEN(
            ["bash", "-c", argv[-1]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            env={"HOME": str(self.remote.root), "PATH": "/usr/bin:/bin"}, cwd=str(self.remote.root),
            start_new_session=True)
        self.procs.append(proc)
        return proc

    def reap(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.fixture
def remote(tmp_path, host, monkeypatch):
    from services.remote_runner import RemoteRunner
    from services.remote_upload import DatasetCache

    fake = FakeRemote(tmp_path / "remote-host")
    # The command file the job is handed begins by sourcing conda (`_CONDA_ACTIVATE_SNIPPET`, which looks in
    # `~/miniforge3` first). This one says it was reached and stops there, whatever conda the test machine has.
    conda_sh = fake.root / "miniforge3" / "etc" / "profile.d" / "conda.sh"
    conda_sh.parent.mkdir(parents=True)
    conda_sh.write_text(f'echo "{SOURCED}"; exit 42\n')
    jobs = _Jobs(fake)
    fake.install(monkeypatch, host, on_launch=jobs)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, zipped, **_k: (self.cache_key(zipped), 0, 0.01))
    # A batched run sends each batch through `upload_subset`; nothing is sent here, as for the single-shot upload.
    monkeypatch.setattr(DatasetCache, "upload_subset", lambda self, *_a, **_k: (0, 0.01, 0))
    monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)
    fake.jobs = jobs
    yield fake
    jobs.reap()


@pytest.fixture
def plate(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "index.json")
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    for well in WELLS:
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            for fov in (1, 2):
                for ch in ("DAPI", "GFP"):
                    zf.writestr(f"e_{ch}_{well}_{fov}_T01.tif", b"x" * 64)
    return inp, out


def _run(plate, host, stop_fetchers, **opts_extra) -> tuple[object, str]:
    """``PipelineRunner._run_pipeline_remote`` — the app's own sequence — over the plate; the runner and its log."""
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import RemoteRunner

    inp, out = plate
    opts = {"filename_schema": SCHEMA, "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", **opts_extra}
    r = pr.PipelineRunner()
    r._remote = host
    r._remote_runner = RemoteRunner(host)
    stop_fetchers(r._remote_runner)
    r._journal_spec = {"opts": {}}
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._open_journal(inp, out, args, opts)
    r._run_pipeline_remote(inp, out, args, opts)
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return r, "".join(str(e[1]) for e in events if e[0] in ("line", "done", "error"))


@pytest.mark.parametrize("batch_size", [0, 2], ids=["single-shot", "batched"])
def test_the_node_is_asked_for_before_planning_and_takes_the_run(plate, host, remote, stop_fetchers, batch_size):
    r, text = _run(plate, host, stop_fetchers, remote_batch_size=batch_size)

    asked = text.find("[remote] scheduler: early: requesting allocation via: env")
    planning = text.find("[remote] planning 2 well archive(s)")
    assert asked != -1 and planning != -1, text
    assert asked < planning, f"the node was asked for after planning began:\n{text}"
    # One job for the run: the early one took it, nothing was launched after staging.
    assert len(remote.launched) == 1, [argv[-1][:200] for argv in remote.launched]
    assert "/reservations/" in remote.launched[0][-1]
    assert "[remote] scheduler: the run is handed to the node asked for early" in text, text
    assert "requesting allocation via: env" not in text.replace("early: requesting", ""), text
    # The job ran the command file it was handed (it begins with conda, which this host answers with a marker)
    # and the run read that from its own stream, after the hand-over.
    handed = text.find("the run is handed to the node asked for early")
    sourced = text.find(SOURCED)
    assert -1 < handed < sourced, text
    pidfile = r._remote_runner._run.remote_pidfile
    assert os.readlink(remote.path(pidfile)).endswith("/run.pid")
    assert "/reservations/" in os.readlink(remote.path(pidfile))


def test_a_job_that_ended_before_the_run_was_ready_is_replaced(plate, host, remote, stop_fetchers, monkeypatch):
    """A job the scheduler or the network ended while the plate was staged: the run says so and asks again."""
    jobs = remote.jobs

    def first_one_ends(argv):
        proc = jobs(argv)
        if len(jobs.procs) == 1:
            proc.kill()
            proc.wait()
        return proc

    remote.install(monkeypatch, host, on_launch=first_one_ends)

    _r, text = _run(plate, host, stop_fetchers)

    assert "the node asked for early cannot take the run" in text, text
    assert "giving back the node asked for early" in text, text
    launches = [argv[-1] for argv in remote.launched]
    assert len(launches) == 2, launches
    assert "/reservations/" in launches[0] and "/reservations/" not in launches[1]
    assert "[remote] scheduler: requesting allocation via: env" in text, text


def test_a_job_slurm_refused_says_why_when_the_run_is_ready(plate, host, remote, stop_fetchers, monkeypatch):
    """Reported: the early job's ``srun`` was refused ("Requested node configuration is not available") and the
    run then said only that the job had ended. The hand-over says the scheduler's own reason, so the line the run
    ends on names the refusal rather than an exit code."""
    jobs = remote.jobs
    refusal = "srun: error: Unable to allocate resources: Requested node configuration is not available"

    def refused_early(argv):
        if not jobs.procs:
            proc = FakeRemote._REAL_POPEN(["sh", "-c", f"echo '{refusal}'; exit 1"], stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, bufsize=1)
            proc.wait()
            jobs.procs.append(proc)
            return proc
        return jobs(argv)

    remote.install(monkeypatch, host, on_launch=refused_early)

    _r, text = _run(plate, host, stop_fetchers)

    assert (f"the node asked for early cannot take the run — it ended before the run was ready (exit 1): "
            f"{refusal}; asking for one now") in text, text


_TIME_LIMIT = "slurmstepd: error: *** STEP {job}.0 ON node1 CANCELLED AT 2026-10-30T10:00:00 DUE TO TIME LIMIT ***"


def _ends_at_its_time_limit(job: int) -> subprocess.Popen:
    proc = FakeRemote._REAL_POPEN(["sh", "-c", f"echo '{_TIME_LIMIT.format(job=job)}'; exit 1"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    proc.wait()
    return proc


def test_an_early_job_cut_off_during_staging_is_not_the_run_s_ending(plate, host, remote, stop_fetchers, monkeypatch):
    """Tracker H5. The early job's output fed the run's evidence, and the first slurmstepd CANCELLED line was kept as
    the run's ending: an early job ended at its time limit while the plate was staged was replaced by a job that ran,
    and the run still said "the scheduler ended job 4242.0 at its time limit". The replacement's own ending decides."""
    jobs = remote.jobs

    def early_cut_off(argv):
        if not jobs.procs:
            jobs.procs.append(_ends_at_its_time_limit(4242))
            return jobs.procs[-1]
        return jobs(argv)

    remote.install(monkeypatch, host, on_launch=early_cut_off)

    _r, text = _run(plate, host, stop_fetchers)

    assert "the node asked for early cannot take the run" in text, text
    assert "was terminated" not in text and "4242" not in text.split("cannot take the run")[-1], text


def test_the_replacement_job_cut_off_is_still_the_run_s_ending(plate, host, remote, stop_fetchers, monkeypatch):
    """The other half: the job the run actually ran on ending at its time limit still says so, naming that job."""
    jobs = remote.jobs

    def both_cut_off(argv):
        jobs.procs.append(_ends_at_its_time_limit(4242 + len(jobs.procs)))
        return jobs.procs[-1]

    remote.install(monkeypatch, host, on_launch=both_cut_off)

    _r, text = _run(plate, host, stop_fetchers)

    assert "Remote pipeline was terminated: the scheduler ended job 4243.0 at its time limit" in text, text


def test_a_run_that_stops_before_it_needs_a_node_gives_the_node_back(plate, host, remote, stop_fetchers):
    """Stopped while the plate is still being staged: the job is cancelled and its directory removed."""
    from services.remote_runner import RemoteRunner

    stop = threading.Event()
    said: list[str] = []
    reservation = RemoteRunner(host).reserve_allocation(n_wells=2, batch_size=0, nanowell=False,
                                                        log_fn=said.append, stop_event=stop)
    assert reservation is not None and reservation.alive()
    assert remote.exists(reservation.paths.dir)
    stop.set()
    deadline = time.monotonic() + 10
    while remote.exists(reservation.paths.dir) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not remote.exists(reservation.paths.dir), said
    assert not reservation.alive()
    assert any("giving back the node asked for early — the run was stopped" in s for s in said), said
    assert reservation.hand_over("true\n", run_pidfile="~/x/run.pid", run_heartbeat="~/x/hb") is None


def test_a_host_with_no_scheduler_asks_for_nothing(host, remote):
    from dataclasses import replace

    from services.remote_runner import RemoteRunner

    direct = replace(host, compute_launcher="")
    assert RemoteRunner(direct).reserve_allocation(n_wells=2, batch_size=0, nanowell=False,
                                                   log_fn=lambda _m: None) is None
    assert remote.launched == []


def test_stale_reservation_directories_are_cleared_and_live_ones_kept(host, remote):
    """A request clears what crashed apps left; a reservation still beating — or a run's, through the link — stays."""
    from services.remote_runner import RemoteRunner

    root = remote.path("~/.allwell-staging/reservations")
    stale, live = root / "stale", root / "live"
    for d in (stale, live):
        d.mkdir(parents=True)
        (d / "heartbeat").touch()
    os.utime(stale / "heartbeat", (time.time() - 3 * 86400,) * 2)
    reservation = RemoteRunner(host).reserve_allocation(n_wells=2, batch_size=0, nanowell=False,
                                                        log_fn=lambda _m: None)
    try:
        assert not stale.exists()
        assert live.exists()
    finally:
        reservation.release("test over")


# ── an early job that waited out Max queue wait (tracker M2) ───────────────────

_QUEUE_WAIT_OUT = ("[remote] scheduler: ERROR no node was granted within 30 min, this host's Max queue wait — "
                   "cancelled job 77 and stopped. Raise Max queue wait for this host in Remotes (Wait indefinitely "
                   "never gives up), or start the run again when the cluster is less busy")


def _waits_out_max_queue_wait() -> subprocess.Popen:
    """What the srun wrapper's early job prints and exits with when Max queue wait runs out before a grant."""
    from services.remote_srun_retry import QUEUE_WAIT_EXIT

    proc = FakeRemote._REAL_POPEN(
        ["sh", "-c", f"echo 'srun: job 77 queued and waiting for resources'; echo \"{_QUEUE_WAIT_OUT}\"; "
                     f"exit {QUEUE_WAIT_EXIT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    proc.wait()
    return proc


def test_an_early_job_that_ended_says_so_when_it_ends_not_at_the_hand_over(host, remote, monkeypatch):
    """Tracker M2. The early job ended while the plate was still being prepared, and nothing was said until the
    run was ready: the status line went on counting "Waiting for the scheduler — 41m (job 77)" for a job that no
    longer existed. Its end is logged as it happens, as a line the pane reads as the early wait's end."""
    from services.pipeline_runner import _sched_events
    from services.remote_runner import RemoteRunner

    jobs = remote.jobs

    def ends_at_once(_argv):
        jobs.procs.append(_waits_out_max_queue_wait())
        return jobs.procs[-1]

    remote.install(monkeypatch, host, on_launch=ends_at_once)
    said: list[str] = []
    reservation = RemoteRunner(host).reserve_allocation(n_wells=2, batch_size=0, nanowell=False,
                                                        log_fn=said.append)
    assert reservation is not None
    try:
        deadline = time.monotonic() + 10
        while not any("has ended" in s for s in said) and time.monotonic() < deadline:
            time.sleep(0.05)
        # No hand-over and no `usable()` yet: the plate is still being prepared.
        states = [e[1][0] for s in said for e in _sched_events(s)]
        assert states == ["waiting-early", "queued", "early-ended"], said
    finally:
        reservation.release("test over")


@pytest.mark.parametrize("batch_size", [0, 2], ids=["single-shot", "batched"])
def test_an_early_job_that_waited_out_max_queue_wait_is_not_asked_for_again(
        plate, host, remote, stop_fetchers, monkeypatch, batch_size):
    """Tracker M2. Max queue wait is how long a run waits for a node. An early job that waited it out during
    staging was replaced by a fresh request at the hand-over, whose wait started again from zero, so a run with a
    30-minute limit could wait an hour. The run stops instead, saying why, and asks for nothing more."""
    jobs = remote.jobs

    def early_waits_out(argv):
        if not jobs.procs:
            jobs.procs.append(_waits_out_max_queue_wait())
            return jobs.procs[-1]
        return jobs(argv)

    remote.install(monkeypatch, host, on_launch=early_waits_out)

    r, text = _run(plate, host, stop_fetchers, remote_batch_size=batch_size)

    assert len(remote.launched) == 1, [argv[-1][:200] for argv in remote.launched]
    assert "[remote] scheduler: requesting allocation via: env" not in text, text
    assert ("the node asked for early was not granted within this host's Max queue wait, so no other is asked "
            "for (it would start the wait again): no node was granted within 30 min, this host's Max queue wait "
            "— cancelled job 77 and stopped.") in text, text
    assert r._had_error, text
