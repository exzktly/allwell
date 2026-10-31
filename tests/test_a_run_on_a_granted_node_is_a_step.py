"""While a node is granted, a run launches as a step of the node's job (tracker H3).

``RemoteRunner.launcher()`` is the one place that decides which scheduler command a run uses. With a granted node it
is ``srun --jobid=<hold> --overlap --nodes=1 --ntasks=1 …``; the composers must then start it plainly (the
``srun`` wrapper's awake-node preference, boot retry and queue deadline are for asking for a node, and a step asks
for none), record ``<job>.<step>`` in the run's sidecar so every teardown acts on the step
(``tests/test_a_run_never_cancels_the_granted_node.py``), and say nothing that the pane would count as a queue wait.

Wrong implementations these reject: wrapping a step in the node-asking wrapper (its ``--exclude`` and ``sinfo``
machinery is wrong for a step, and its deadline would cancel it as an unanswered request); writing the bare job id to
the sidecar (the end of the run would cancel the node); changing an ordinary run's launch while doing this; and
printing the request line that starts a queue wait.
"""

from __future__ import annotations

import pytest

from services.remote_commands import _compose_driver_cmd, _compose_remote_cmd
from services.remote_config import RemoteHost
from services.remote_hold import HoldState, StepTarget, parse_state, step_launcher
from services.remote_process import _scheduler_handoff_lines
from services.remote_runner import RemoteRunner
from services.slurm_ids import is_step_launcher, launcher_job_id

PIDFILE = "~/.allwell-staging/runs/r1/run.pid"
TARGET = StepTarget(job_id="4001", node="gpu-node-1", cpus=8, gres="gpu:1")
STEP = step_launcher(TARGET)
ORDINARY = "srun --partition=gpu --gres=gpu:1 --cpus-per-task=8 --time=4:00:00"


def _single(launcher: str) -> str:
    return _compose_remote_cmd(launcher, PIDFILE, ["python", "pipeline.py"], cache_hit=False)


def _driver(launcher: str) -> str:
    return _compose_driver_cmd(launcher, PIDFILE, "echo driver")


# ── the launcher ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_step_launcher_names_the_job_the_cpus_and_the_gres():
    assert STEP == "srun --jobid=4001 --overlap --nodes=1 --ntasks=1 --cpus-per-task=8 --gres=gpu:1"
    assert step_launcher(StepTarget(job_id="4001")) == "srun --jobid=4001 --overlap --nodes=1 --ntasks=1"
    assert is_step_launcher(STEP) and launcher_job_id(STEP) == "4001"
    assert not is_step_launcher(ORDINARY) and launcher_job_id(ORDINARY) == ""
    assert launcher_job_id("srun --jobid 77 bash") == "77" and is_step_launcher("srun --jobid 77 bash")


def test_the_runner_launches_as_a_step_only_while_a_node_is_granted():
    host = RemoteHost(name="h", ssh_alias="h", compute_launcher=ORDINARY)
    runner = RemoteRunner(host)
    assert runner.launcher() == host.launch_command == ORDINARY
    runner.step_target = TARGET
    assert runner.launcher() == STEP
    runner.step_target = None
    assert runner.launcher() == ORDINARY
    # `--exclusive`, which the host adds to a run's own request, is not added to a step of a node already granted.
    exclusive = RemoteRunner(RemoteHost(name="h", ssh_alias="h", compute_launcher=ORDINARY, exclusive_node=True))
    exclusive.step_target = TARGET
    assert "--exclusive" not in exclusive.launcher()
    assert "--exclusive" in RemoteRunner(exclusive.host).launcher()


# ── what is composed for a step ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("compose", [_single, _driver], ids=["single-shot", "batch-driver"])
def test_a_step_is_started_plainly_not_through_the_node_asking_wrapper(compose):
    """Wrong implementation: wrapping it. The wrapper reads `sinfo`, excludes nodes, retries a boot and cancels a
    request that is not granted within the queue wait — none of which is a step's business."""
    cmd = compose(STEP)
    assert cmd.startswith('echo $$ > "$HOME"/.allwell-staging/runs/r1/run.pid; exec srun --jobid=4001 --overlap ')
    for wrapper_machinery in ("sinfo", "--test-only", "_aw_try", "_aw_awake", "--exclude"):
        assert wrapper_machinery not in cmd
    ordinary = compose(ORDINARY)
    assert "_aw_try" in ordinary and "sinfo" in ordinary, "an ordinary run must keep the wrapper"


@pytest.mark.parametrize("compose", [_single, _driver], ids=["single-shot", "batch-driver"])
def test_a_step_records_job_dot_step_in_the_sidecar_and_an_ordinary_run_the_job(compose):
    """Wrong implementation: the bare `$SLURM_JOB_ID`, which the end of the run would `scancel` — the node."""
    sidecar = '> "$HOME"/.allwell-staging/runs/r1/run.pid.slurm'
    assert f'echo "${{SLURM_JOB_ID:-}}.${{SLURM_STEP_ID:-}}" {sidecar}' in compose(STEP)
    ordinary = compose(ORDINARY)
    assert f'echo "${{SLURM_JOB_ID:-}}" {sidecar}' in ordinary
    assert "SLURM_STEP_ID" not in ordinary


@pytest.mark.parametrize("compose", [_single, _driver], ids=["single-shot", "batch-driver"])
def test_the_granted_line_still_starts_with_the_text_the_pane_reads_and_says_it_is_a_step(compose):
    from services.remote_srun_retry import ALLOCATION_GRANTED

    cmd = compose(STEP)
    assert f"{ALLOCATION_GRANTED} — job ${{SLURM_JOB_ID:-?}} on " in cmd
    assert "a step on the granted node" in cmd
    assert "a step on the granted node" not in compose(ORDINARY)


# ── what the run log says ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_step_announces_no_queue_wait_and_an_ordinary_run_still_does():
    """Wrong implementation: the request line, which the Analyze pane counts a wait from."""
    step_line = _scheduler_handoff_lines(STEP)
    assert step_line == "[remote] scheduler: running on the granted node (job 4001) — no queue wait.\n"
    assert "requesting allocation via" not in step_line
    assert "requesting allocation via: " + ORDINARY in _scheduler_handoff_lines(ORDINARY)
    assert _scheduler_handoff_lines("") == ""


# ── the GRES a step asks for ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scontrol, gres", [
    ("TresPerNode=gres/gpu:1", "gpu:1"),                 # Slurm 21.08+
    ("TresPerNode=gres:gpu:a10g:2", "gpu:a10g:2"),
    ("Gres=gpu:1", "gpu:1"),                              # older
    ("TresPerNode=gres/gpu:1,gres/shard:2", "gpu:1,shard:2"),
    ("TresPerNode=(null)", ""),
    ("", ""),
])
def test_the_jobs_gres_is_read_as_gres_takes_it(scontrol, gres):
    text = f"##job\n4001\n##scontrol\nJobId=4001 JobState=RUNNING Reason=None NodeList=n1 {scontrol}\n##granted\n" \
           "node=n1\ncpus=8\ngranted_at=1\nlimit_s=3600\n##status\nstate=idle\nidle_s=0\n##ended\n"
    state = parse_state(text)
    assert state.gres == gres
    assert StepTarget.from_state(state) == StepTarget(job_id="4001", node="n1", cpus=8, gres=gres)
    assert HoldState().gres == ""
