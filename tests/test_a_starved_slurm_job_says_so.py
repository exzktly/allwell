"""A Slurm job given one core of a big node says so in the run log.

A scheduler command with no CPU request gets Slurm's minimum — one core, two
CPUs on a hyperthreaded node — and the worker pool is sized from the CPUs the
job has. The reported run used one worker on a 16-core GPU node, and
``--workers 6`` was clamped to one with "exceeds available CPU budget (1)",
which named the number but not the cause or the fix. These drive
:func:`pipeline.parallelism.plan_parallelism`, the call the run makes, and read
what it logs.
"""

from __future__ import annotations

import logging
import types

import pytest

import pipeline.parallelism as par


def _args(**over):
    base = dict(tf_threads=0, workers=0, cpu_only=False,
                segmentation_method="cellpose_labelfree")
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def job(monkeypatch):
    """A process inside a Slurm job on a 16-CPU Linux node with one GPU."""
    def _set(*, cpus: int, slurm: bool = True, cpus_per_task: str | None = None,
             node: int = 16):
        monkeypatch.setattr(par, "_cpu_count", lambda: cpus)
        monkeypatch.setattr(par.os, "cpu_count", lambda: node)
        monkeypatch.setattr(par.sys, "platform", "linux")
        monkeypatch.setattr(par, "visible_gpu_count", lambda: 1)
        for k in ("SLURM_JOB_ID", "SLURM_CPUS_PER_TASK"):
            monkeypatch.delenv(k, raising=False)
        if slurm:
            monkeypatch.setenv("SLURM_JOB_ID", "523661")
        if cpus_per_task is not None:
            monkeypatch.setenv("SLURM_CPUS_PER_TASK", cpus_per_task)
    return _set


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_a_job_with_no_cpu_request_names_the_cause_and_the_fix(job, caplog):
    job(cpus=2)
    with caplog.at_level(logging.WARNING, logger="t"):
        plan = par.plan_parallelism(_args(workers=6), logging.getLogger("t"))
    assert plan.workers == 1
    assert ("Slurm gave this job 2 of the node's 16 CPUs — the scheduler command "
            "asks for no CPUs, so Slurm gave the job its minimum. The run has one "
            "worker, and --workers cannot raise that. Add --cpus-per-task=<number "
            "of cores> to the scheduler command in the remote's settings (up to 16 "
            "on this node).") in _warnings(caplog)


def test_a_small_explicit_request_is_named_as_such(job):
    job(cpus=1, cpus_per_task="1")
    assert "asks for --cpus-per-task=1" in par.slurm_cpu_shortfall(1)


@pytest.mark.parametrize("cpus, slurm, node", [
    (16, True, 16),   # the job has the node
    (4, True, 16),    # more than one core asked for: a choice, not a starvation
    (2, False, 16),   # not under Slurm: a small laptop is not a misconfiguration
    (2, True, 2),     # the node has nothing more to give
])
def test_nothing_is_said_when_nothing_is_wrong(job, caplog, cpus, slurm, node):
    job(cpus=cpus, slurm=slurm, node=node)
    assert par.slurm_cpu_shortfall(cpus) == ""
    with caplog.at_level(logging.WARNING, logger="t"):
        par.plan_parallelism(_args(), logging.getLogger("t"))
    assert not any("Slurm gave this job" in w for w in _warnings(caplog))
