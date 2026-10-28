"""A launch runs one copy of the job, however many tasks the environment asks ``srun`` for.

A batched run failed batch 1 with ``mv: cannot stat '…/b1': No such file or directory`` and "could not stage its
input", after the batch's zips had uploaded. Its log showed "allocation granted" five times, all with one job
number, and the launcher carried no task count. ``srun`` starts the job script once per task, and it reads
``SLURM_NTASKS`` from the environment as it reads ``--ntasks``: five batch drivers ran over one run directory, all
five found ``b1/``, one moved it into ``in/``, and the next deleted that ``in/`` and failed to move a ``b1/`` that
was gone. The launch now asks for ``--nodes=1 --ntasks=1`` explicitly (``remote_srun_retry.one_task``), and the job
script's first line sends any copy but the first task away (``remote_commands._FIRST_TASK_ONLY``).

These run the real composed launch under ``sh`` with a stand-in ``srun`` that does what Slurm does with a task
count — one concurrent copy of the script per task, each with its own ``SLURM_PROCID`` — over the real batch
driver.
"""

from __future__ import annotations

import shlex
import stat
import subprocess
import zipfile

import pytest

from services import remote_commands as rc
from services.remote_commands import BATCH_READY
from services.remote_srun_retry import one_task

#: Starts one copy of everything after ``bash`` per task: the last ``--ntasks=`` it was given, else
#: ``$SLURM_NTASKS``, else one — or ``$SLURM_NTASKS`` whatever it was given when ``$S/ignore_ntasks`` exists, a
#: cluster that starts more anyway. Records its arguments.
_SRUN = r'''#!/bin/sh
printf '%s\n' "$*" > "$S/args"
n=${SLURM_NTASKS:-1}
for a in "$@"; do case "$a" in --ntasks=*) n=${a#--ntasks=};; bash) break;; esac; done
[ -f "$S/ignore_ntasks" ] && n=${SLURM_NTASKS:-1}
echo "srun: job 77 has been allocated resources" >&2
while [ "$1" != "bash" ]; do shift; done
i=0; while [ "$i" -lt "$n" ]; do SLURM_PROCID=$i SLURM_STEP_NUM_TASKS=$n "$@" & i=$((i+1)); done
wait
'''


def _cluster(tmp_path):
    bindir, state = tmp_path / "bin", tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    srun = bindir / "srun"
    srun.write_text(_SRUN)
    srun.chmod(srun.stat().st_mode | stat.S_IEXEC)
    return bindir, state


def _launch_one_batch(tmp_path, *, ntasks_env: str, ignore_ntasks: bool = False):
    """Batch 1 staged and flagged ready; launch the driver as the app does. Returns (the launch's output, what each
    pipeline run saw in ``in/``, the arguments ``srun`` was given)."""
    bindir, state = _cluster(tmp_path)
    if ignore_ntasks:
        (state / "ignore_ntasks").touch()
    run_dir = tmp_path / "run"
    (run_dir / "b1").mkdir(parents=True)
    with zipfile.ZipFile(run_dir / "b1" / "A01.zip", "w") as z:
        z.writestr("A01_F001_T001_DAPI.tif", b"x")
    (run_dir / BATCH_READY.format(i=1)).touch()
    seen = tmp_path / "seen"
    # The pipeline: one line per run, naming what its input directory held.
    pipeline = ["sh", "-c", f'ls {shlex.quote(str(run_dir / "in"))} | tr "\\n" " " >> {shlex.quote(str(seen))}; '
                            f'echo >> {shlex.quote(str(seen))}']
    inner = ('echo "[remote] scheduler: allocation granted — job ${SLURM_JOB_ID:-77}"; '
             + rc._batch_driver_snippet(str(run_dir), 1, pipeline, "", 0))
    cmd = rc._launch("srun -p cpu", inner, str(tmp_path / "run.pid"))
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path), "S": str(state), "SLURM_NTASKS": ntasks_env}
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60)
    runs = seen.read_text().splitlines() if seen.exists() else []
    return out.stdout + out.stderr, runs, (state / "args").read_text()


def test_a_task_count_from_the_environment_starts_one_driver(tmp_path):
    log, runs, args = _launch_one_batch(tmp_path, ntasks_env="5")
    assert "--nodes=1 --ntasks=1" in args
    assert log.count("allocation granted") == 1, log
    assert "could not stage its input" not in log, log
    assert runs == ["A01.zip "], "batch 1 ran once, over its own input"


def test_a_cluster_that_starts_more_anyway_runs_the_job_once(tmp_path):
    log, runs, _args = _launch_one_batch(tmp_path, ntasks_env="5", ignore_ntasks=True)
    assert log.count("allocation granted") == 1, log
    assert "WARNING the launch started 5 copies of the job" in log
    assert "could not stage its input" not in log, log
    assert runs == ["A01.zip "]


@pytest.mark.parametrize(("launcher", "expected"), [
    (["srun", "-p", "cpu"], ["srun", "-p", "cpu", "--nodes=1", "--ntasks=1"]),
    (["srun", "-n", "4", "-c", "2", "-N2"], ["srun", "--cpus-per-task=8", "--nodes=1", "--ntasks=1"]),
    (["srun", "-n4", "--cpus-per-task=3"], ["srun", "--cpus-per-task=12", "--nodes=1", "--ntasks=1"]),
    (["srun", "--ntasks", "16", "--ntasks-per-node=16"], ["srun", "--cpus-per-task=16", "--nodes=1", "--ntasks=1"]),
    (["srun", "--nodes", "3", "-c", "4", "--mem=4G"], ["srun", "-c", "4", "--mem=4G", "--nodes=1", "--ntasks=1"]),
    (["srun", "--ntasks=x", "-c", "2"], ["srun", "--cpus-per-task=2", "--nodes=1", "--ntasks=1"]),
])
def test_a_task_count_in_the_launcher_becomes_cpus_for_one_task(launcher, expected):
    """``-n`` in a launcher is how a job template asks for cores; folded as the JupyterHub path folds it."""
    assert one_task(launcher) == expected
