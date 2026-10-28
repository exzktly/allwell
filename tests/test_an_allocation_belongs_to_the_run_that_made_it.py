"""A scheduler job is cancelled only by the run that asked for it.

``run_id`` is deterministic in the plate and the settings, so two sessions on
the same plate — the second window a user opens after the first one goes quiet —
meet in one remote run directory and read one ``run.pid.slurm`` sidecar. The
second session is refused ("another session is already running this exact
dataset"), and the refusal used to be followed by the runner's guaranteed
teardown, which read that shared sidecar and ``scancel``\\ ed the *first*
session's live job: the refusal that was meant to protect the running plate
was what killed it.

These drive the app's own sequence — ``PipelineRunner._run_pipeline_remote``,
the real ``RemoteRunner`` and staging — against a host that runs its commands
for real (``tests/_fake_remote.py``), with stand-in ``squeue`` and ``scancel``
on the host's ``PATH``. The stand-ins keep a job table on disk, so what is
asserted is what the scheduler was asked to do.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from tests._fake_remote import FakeRemote

SCHEMA = "experiment:channel:well:fov:timepoint"
WELLS = ("A01", "B02")
LIVE_JOB = "4242"


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def scheduler_bin(bindir: Path, jobs: Path) -> None:
    """Stand-in ``squeue`` / ``scancel`` over a job table, one file per job.

    ``jobs/<id>`` holds the job's state (``RUNNING``, ``PENDING`` …); a job with
    no file has ended and been purged, which real ``squeue -j`` answers with
    ``Invalid job id specified`` and exit 1. ``-o`` is honoured for ``%T``
    (state), ``%u`` (owner — always the user running the test) and ``%i``.
    ``scancel`` records its arguments in ``jobs/../scancelled`` and ends the job.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    jobs.mkdir(parents=True, exist_ok=True)
    j = shlex.quote(str(jobs))
    _exe(bindir / "squeue", (
        f"J={j}\n"
        'fmt="%i"; id=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -j) id=$2; shift;; -o) fmt=$2; shift;; esac; shift; done\n'
        'if [ ! -f "$J/$id" ]; then echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1; fi\n'
        'st=$(cat "$J/$id"); me=$(id -un)\n'
        'printf "%s\\n" "$fmt" | sed -e "s/%T/$st/g" -e "s/%u/$me/g" -e "s/%i/$id/g"\n'))
    _exe(bindir / "scancel", (
        f"J={j}\n"
        'printf "%s\\n" "$*" >> "$J/../scancelled"\n'
        'for a in "$@"; do case "$a" in -*) ;; *) [ -f "$J/$a" ] && rm -f "$J/$a";; esac; done\n'
        "exit 0\n"))


def scancelled(jobs: Path) -> list[str]:
    log = jobs.parent / "scancelled"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def host():
    from services.remote_config import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
        compute_launcher="srun --time=1:00:00",
    )


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


@pytest.fixture
def remote(tmp_path, host, monkeypatch):
    from services.remote_runner import RemoteRunner
    from services.remote_upload import DatasetCache

    fake = FakeRemote(tmp_path / "remote-host")
    fake.bin_dir = tmp_path / "remote-bin"
    fake.jobs = tmp_path / "scheduler" / "jobs"
    scheduler_bin(fake.bin_dir, fake.jobs)

    def _launched(_argv):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.pid = 4242
        return proc

    fake.install(monkeypatch, host, on_launch=_launched)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, zipped, **_k: (self.cache_key(zipped), 0, 0.01))
    monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)
    return fake


def _opts(**extra):
    return {"filename_schema": SCHEMA, "filename_sep": "_",
            "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", **extra}


def _pipeline_runner(host, stop_fetchers):
    import services.pipeline_runner as pr
    from services.remote_runner import RemoteRunner

    r = pr.PipelineRunner()
    r._remote = host
    r._remote_runner = RemoteRunner(host)
    stop_fetchers(r._remote_runner)
    r._journal_spec = {"opts": {}}
    return r


def _first_session_is_running(plate, host, remote, stop_fetchers) -> str:
    """Session one: launched, granted job ``LIVE_JOB``, still beating.

    Spawned through the real ``RemoteRunner`` so its run directory is the one
    the plate's ``run_id`` names; the allocation it was granted is then put on
    the host by hand — the sidecar the job writes from inside it, a fresh
    heartbeat, and the job running in the scheduler's table. Returns the run
    directory.
    """
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import OutputFilter, stage_local_zips

    inp, out = plate
    r = _pipeline_runner(host, stop_fetchers)
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, _opts())
    r._open_journal(inp, out, args, _opts())
    zipped = stage_local_zips(inp, filename_schema=SCHEMA, filename_sep="_",
                              prior_output_dir=out)
    proc = r._remote_runner.spawn(zipped, out, args, output_filter=OutputFilter.FULL)
    hb = getattr(proc, "_heartbeat", None)
    if hb is not None:
        hb.stop()
    pidfile = r._remote_runner._run.remote_pidfile
    remote.path(pidfile + ".slurm").write_text(LIVE_JOB + "\n")
    remote.path(os.path.dirname(pidfile) + "/.aw_upload_alive").touch()
    (remote.jobs / LIVE_JOB).write_text("RUNNING")
    r._close_journal()
    return os.path.dirname(pidfile)


def _second_session(plate, host, stop_fetchers, **opts_extra):
    from services.pipeline_service import build_pipeline_args

    inp, out = plate
    opts = _opts(**opts_extra)
    r = _pipeline_runner(host, stop_fetchers)
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._open_journal(inp, out, args, opts)
    r._run_pipeline_remote(inp, out, args, opts)
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return r, "".join(str(e[1]) for e in events if e[0] in ("line", "done", "error"))


@pytest.mark.parametrize("batch_size", [2, 0], ids=["batched", "single-shot"])
def test_a_refused_second_session_leaves_the_first_session_s_job_running(
        plate, host, remote, stop_fetchers, batch_size):
    run_dir = _first_session_is_running(plate, host, remote, stop_fetchers)
    launches = len(remote.launched)

    r, text = _second_session(plate, host, stop_fetchers, remote_batch_size=batch_size)
    if r._remote_runner._run is not None:
        # The fixture measures something only if both sessions met in one directory.
        assert os.path.dirname(r._remote_runner._run.remote_pidfile) == run_dir, text

    assert scancelled(remote.jobs) == [], (
        f"the refused second session cancelled the first session's job:\n{text}")
    assert (remote.jobs / LIVE_JOB).read_text() == "RUNNING"
    assert len(remote.launched) == launches, (
        f"the second session launched a second allocation into the first one's run directory:\n{text}")
    assert "another session" in text, text
    assert remote.path(run_dir + "/run.pid.slurm").read_text().strip() == LIVE_JOB, (
        "the first session's record of its job was removed")


def test_a_run_that_launched_its_own_job_still_cancels_it(plate, host, remote, stop_fetchers):
    """The control: the run that asked for the allocation still hands it back."""
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import OutputFilter, stage_local_zips

    inp, out = plate
    r = _pipeline_runner(host, stop_fetchers)
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, _opts())
    zipped = stage_local_zips(inp, filename_schema=SCHEMA, filename_sep="_",
                              prior_output_dir=out)
    proc = r._remote_runner.spawn(zipped, out, args, output_filter=OutputFilter.FULL)
    hb = getattr(proc, "_heartbeat", None)
    if hb is not None:
        hb.stop()
    pidfile = r._remote_runner._run.remote_pidfile
    remote.path(pidfile + ".slurm").write_text("5151\n")
    (remote.jobs / "5151").write_text("RUNNING")
    said: list[str] = []
    r._remote_runner.close_remote_job(log_fn=said.append)
    assert scancelled(remote.jobs) == [f"-u {_me()} 5151"], said
    assert "cancelled lingering job 5151" in "".join(said)


def _me() -> str:
    return subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()


# ── a resume and the earlier attempt it resumes (review E2) ─────────────────
#
# A resume is incremental, so its staged archives — and therefore its run_id
# and run directory — differ from the attempt it resumes, while it writes into
# that attempt's output directory and takes its archives. The sweep and the
# "owned" guard looked only at the resume's own directory, so an earlier
# attempt's job still running there was never seen.

EARLIER_JOB = "7777"


def _an_earlier_attempt_is_still_running(plate, host, remote, stop_fetchers, *,
                                         heartbeat_age: float) -> str:
    """The journal records an earlier attempt, ``run-earlier``, whose job is
    still alive in the scheduler; its app last beat *heartbeat_age* s ago.

    Recorded through the real spawn — so the journal keys are the ones a
    resume computes — and then moved to a run id of its own, which is what a
    resume's changed archives do. Returns that attempt's run directory.
    """
    run_dir = _first_session_is_running(plate, host, remote, stop_fetchers)
    for leaf in ("run.pid.slurm", ".aw_upload_alive"):
        remote.path(f"{run_dir}/{leaf}").unlink()
    (remote.jobs / LIVE_JOB).unlink()
    earlier = run_dir.rsplit("/", 1)[0] + "/run-earlier"
    _inp, out = plate
    payload = J._read_run_payload(out)
    payload["remote"].update(run_id="run-earlier", remote_output_dir=f"{earlier}/out",
                             remote_input_dir=f"{earlier}/in")
    assert J._atomic_write_json(J._run_path(out), payload)
    remote.path(f"{earlier}/out").mkdir(parents=True)
    remote.path(f"{earlier}/run.pid.slurm").write_text(EARLIER_JOB + "\n")
    os.utime(remote.path(f"{earlier}/run.pid.slurm"), (1_700_000_000,) * 2)
    beat = remote.path(f"{earlier}/.aw_upload_alive")
    beat.touch()
    import time
    os.utime(beat, (time.time() - heartbeat_age,) * 2)
    (remote.jobs / EARLIER_JOB).write_text("RUNNING")
    return earlier


@pytest.mark.parametrize("batch_size", [2, 0], ids=["batched", "single-shot"])
def test_a_resume_cancels_the_earlier_attempt_s_abandoned_job(
        plate, host, remote, stop_fetchers, batch_size):
    """No app has fed it for ten minutes: it is this user's own abandoned
    attempt at the plate, and left running it would write the output the
    resume is about to write. Cancelled, and the log says whose it was."""
    earlier = _an_earlier_attempt_is_still_running(
        plate, host, remote, stop_fetchers, heartbeat_age=600)
    launches = len(remote.launched)

    r, text = _second_session(plate, host, stop_fetchers, remote_batch_size=batch_size)

    assert scancelled(remote.jobs) == [f"-u {_me()} {EARLIER_JOB}"], text
    assert f"cancelled a job left over from an earlier run of this dataset ({EARLIER_JOB}, " \
           f"in an earlier attempt's run directory, {earlier})" in text, text
    assert len(remote.launched) == launches + 1, f"the resume did not go ahead:\n{text}"


@pytest.mark.parametrize("batch_size", [2, 0], ids=["batched", "single-shot"])
def test_a_resume_is_refused_while_the_earlier_attempt_is_still_fed(
        plate, host, remote, stop_fetchers, batch_size):
    """Its heartbeat is fresh: an app — another window — is still running it.
    Cancelling would be cancelling that window's run; going ahead would be two
    pipelines writing one output. The run stops and says what to do."""
    earlier = _an_earlier_attempt_is_still_running(
        plate, host, remote, stop_fetchers, heartbeat_age=0)
    launches = len(remote.launched)

    r, text = _second_session(plate, host, stop_fetchers, remote_batch_size=batch_size)

    assert scancelled(remote.jobs) == [], text
    assert len(remote.launched) == launches, f"a second pipeline was launched:\n{text}"
    assert (f"another session is already running this exact dataset and configuration on this "
            f"host (job {EARLIER_JOB}, in an earlier attempt's run directory, {earlier}") in text, text
    assert f"scancel {EARLIER_JOB}" in text
    assert (remote.jobs / EARLIER_JOB).read_text() == "RUNNING"
