"""Asking for a granted node (``services.remote_hold.GrantedNode``): the ``sbatch`` it sends, how it waits, and how it
gives the node back.

The scripts the app sends to the host are run for real under ``bash``, with stand-in ``sbatch``, ``scontrol``,
``scancel``, ``sinfo`` and ``squeue`` programs on the PATH and a throwaway HOME as the "remote" home directory
(the same approach as ``test_srun_boot_retry.py``). What the stand-ins do is driven by the test: a queue of job
states that advances each time the request naps, so a wait of minutes takes no time.

Wrong implementations these are written to reject: passing the scheduler command's own ``--time`` to the hold job;
resubmitting after a boot failure without excluding the node that failed; waiting for ever when the queue wait has
run out; forgetting to cancel the job when the request is abandoned.
"""

from __future__ import annotations

import shutil

import pytest

from services.remote_config import RemoteHost
from services.remote_hold import (
    GrantedNode,
    HoldError,
    HoldQueueWaitSpent,
    HoldState,
    cancel_script,
    hold_paths,
    parse_partition_limit,
    parse_state,
    sbatch_options,
)
from services.remote_srun_retry import BOOT_RETRY_PAUSE_S

from tests._hold_cluster import HOST, Cluster as _Cluster, Script as _Script, granted_files as _gf

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

@pytest.fixture
def cluster(tmp_path):
    return _Cluster(tmp_path)


@pytest.fixture
def nodes():
    made: list[GrantedNode] = []
    yield made
    for node in made:
        node.release("test over")      # stops the heartbeat thread


def _node(cluster, nodes, sleep, **host_kw) -> tuple[GrantedNode, RemoteHost, list[str]]:
    host = RemoteHost(**{**HOST, **host_kw})
    lines: list[str] = []
    node = GrantedNode(host, cluster, log_fn=lines.append, sleep=sleep, clock=sleep.clock)
    nodes.append(node)
    return node, host, lines


def _granted_files(cluster, host, node="gpu-node-1", gpus="NVIDIA A10G") -> None:
    _gf(cluster, host, node, gpus)


def _opt(args: list[str], prefix: str) -> list[str]:
    return [a for a in args if a == prefix or a.startswith(prefix + "=")]


# ── the request ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_request_sends_sbatch_with_the_hosts_options_and_the_holds_own_time_limit(cluster, nodes):
    host = RemoteHost(**{**HOST, "granted_time_limit_h": 48})
    sleep = _Script(cluster, [
        ("PENDING", "Resources", ""),
        ("RUNNING", "None", "gpu-node-1"),
    ])
    sleep.steps.insert(1, lambda: _granted_files(cluster, host))
    node, _, lines = _node(cluster, nodes, sleep, granted_time_limit_h=48)
    state = node.request()

    assert state.phase in ("granted", "busy") and state.node == "gpu-node-1" and state.gpus == "NVIDIA A10G"
    (args,) = cluster.sbatch_calls()
    assert "--parsable" in args and "--job-name=allwell-hold" in args
    assert "--partition=gpu" in args and "--gres=gpu:1" in args and "--cpus-per-task=8" in args
    assert "--nodes=1" in args and "--ntasks=1" in args
    # The hold's own time limit (48 h), not the 4 h the scheduler command asks for one run.
    assert _opt(args, "--time") == ["--time=2-00:00:00"]
    assert not any("4:00:00" in a for a in args)
    assert args[-1].endswith("/hold.sh") and any(a.startswith("--output=") and a.endswith("/log") for a in args)
    # The job id was recorded, the script written, the app's heartbeat is there for the script to read.
    hold_dir = cluster.hold_dir(host)
    assert (hold_dir / "job").read_text(encoding="utf-8") == "4001"
    assert "_aw_end" in (hold_dir / "hold.sh").read_text(encoding="utf-8")
    assert (hold_dir / "heartbeat").exists()
    assert any("is granted" in line and "gpu-node-1" in line for line in lines)


def test_the_partition_limit_lowers_the_time_limit_and_infinite_does_not(cluster, nodes):
    host = RemoteHost(**HOST)
    for cap, expected in (("12:00:00\n", "--time=12:00:00"), ("infinite\n", "--time=1-00:00:00")):
        (cluster.fake / "sinfo.out").write_text(cap, encoding="utf-8")
        for p in cluster.fake.glob("sbatch.*.args"):
            p.unlink()
        (cluster.fake / "next_id").unlink(missing_ok=True)       # a fresh cluster each time round
        sleep = _Script(cluster, [("RUNNING", "None", "n1")])
        sleep.steps.insert(0, lambda: _granted_files(cluster, host))
        node, _, lines = _node(cluster, nodes, sleep)
        node.request()
        (args,) = cluster.sbatch_calls()
        assert _opt(args, "--time") == [expected]
        assert any("lowered" in line for line in lines) == (cap != "infinite\n")
        node.release("next case")
        (cluster.hold_dir(host) / "job").unlink(missing_ok=True)


def test_the_default_partition_is_asked_about_when_the_command_names_none(cluster, nodes):
    (cluster.fake / "sinfo.out").write_text("gpu* 6:00:00\n", encoding="utf-8")
    host = RemoteHost(**{**HOST, "compute_launcher": "srun --gres=gpu:1"})
    sleep = _Script(cluster, [lambda: _granted_files(cluster, host), ("RUNNING", "None", "n1")])
    node, _, _ = _node(cluster, nodes, sleep, compute_launcher="srun --gres=gpu:1")
    node.request()
    assert _opt(cluster.sbatch_calls()[0], "--time") == ["--time=06:00:00"]


def test_a_node_that_waits_past_the_max_queue_wait_is_cancelled_and_reported(cluster, nodes):
    """Wrong implementation: waiting for ever (the stand-in sleep fails the test after 80 naps)."""
    host = RemoteHost(**{**HOST, "queue_wait_timeout_min": 1})
    sleep = _Script(cluster, [])
    node, _, _ = _node(cluster, nodes, sleep, queue_wait_timeout_min=1)
    with pytest.raises(HoldQueueWaitSpent) as exc:
        node.request()
    assert "within 1 min" in str(exc.value) and "Max queue wait" in str(exc.value)
    assert any("4001" in line for line in cluster.scancels()), "the queued job must be cancelled"
    assert sum(sleep.naps) >= 60
    assert (cluster.hold_dir(host) / "release").exists()


def test_a_node_that_fails_to_boot_is_not_asked_for_again(cluster, nodes):
    """Wrong implementation: resubmitting the same request, which Slurm may well give the same dead node."""
    host = RemoteHost(**{**HOST, "compute_launcher": HOST["compute_launcher"] + " --exclude=slow1"})
    sleep = _Script(cluster, [])

    def second_job_runs():
        cluster.set_job(4002, "RUNNING", "None", "gpu-node-2")
        _granted_files(cluster, host, node="gpu-node-2")

    # The first job's node never comes up; the retry's job (4002) is queued, then runs.
    sleep.steps = [("CONFIGURING", "None", "dead-1"), ("BOOT_FAIL", "None", "dead-1"), lambda: None,
                   second_job_runs]
    node, _, lines = _node(cluster, nodes, sleep, compute_launcher=HOST["compute_launcher"] + " --exclude=slow1")
    state = node.request()
    first, second = cluster.sbatch_calls()
    assert _opt(first, "--exclude") == ["--exclude=slow1"]
    assert _opt(second, "--exclude") == ["--exclude=slow1,dead-1"]          # the user's, and the one that failed
    assert state.node == "gpu-node-2"
    assert BOOT_RETRY_PAUSE_S in sleep.naps
    assert any("dead-1 failed to boot" in line for line in lines)


def test_an_option_the_scheduler_refuses_is_shown_in_the_schedulers_words(cluster, nodes):
    (cluster.fake / "sbatch.refuse").touch()
    node, host, _ = _node(cluster, nodes, _Script(cluster, []))
    with pytest.raises(HoldError, match="Requested node configuration is not available"):
        node.request()


def test_a_request_abandoned_while_queued_cancels_the_job_and_leaves_the_release_marker(cluster, nodes):
    """Wrong implementation: leaving the queued job to be granted a node nobody wants."""
    host = RemoteHost(**HOST)
    holder: dict = {}
    sleep = _Script(cluster, [lambda: None, lambda: holder["node"].release("the user pressed Release")])
    node, _, _ = _node(cluster, nodes, sleep)
    holder["node"] = node
    with pytest.raises(HoldError, match="cancelled"):
        node.request()
    assert any("4001" in line for line in cluster.scancels())
    assert (cluster.hold_dir(host) / "release").exists()


def test_a_second_request_is_refused_while_a_node_is_live(cluster, nodes):
    host = RemoteHost(**HOST)
    sleep = _Script(cluster, [lambda: _granted_files(cluster, host), ("RUNNING", "None", "n1")])
    first, _, _ = _node(cluster, nodes, sleep)
    first.request()
    second, _, _ = _node(cluster, nodes, _Script(cluster, []))
    with pytest.raises(HoldError, match="already"):
        second.request()
    assert len(cluster.sbatch_calls()) == 1


def test_a_stale_hold_directory_is_cleared_before_a_new_request(cluster, nodes):
    host = RemoteHost(**HOST)
    d = cluster.hold_dir(host)
    d.mkdir(parents=True)
    for name in ("ended", "granted", "release", "busy", "status"):
        (d / name).write_text("old\n", encoding="utf-8")
    sleep = _Script(cluster, [lambda: _granted_files(cluster, host), ("RUNNING", "None", "n1")])
    node, _, _ = _node(cluster, nodes, sleep)
    state = node.request()
    assert state.phase in ("granted", "busy")
    assert not (d / "ended").exists() and not (d / "release").exists() and not (d / "busy").exists()


# ── release ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_release_asks_the_script_to_end_and_cancels_only_this_users_job(cluster, nodes):
    host = RemoteHost(**HOST)
    d = cluster.hold_dir(host)
    d.mkdir(parents=True)
    (d / "job").write_text("4001", encoding="utf-8")
    node, _, lines = _node(cluster, nodes, _Script(cluster, []))
    node.release("test")
    assert (d / "release").exists()
    assert any("-u" in line and "4001" in line for line in cluster.scancels())
    assert any("released" in line for line in lines)


def test_release_leaves_a_job_that_belongs_to_someone_else(cluster, nodes):
    host = RemoteHost(**HOST)
    d = cluster.hold_dir(host)
    d.mkdir(parents=True)
    (d / "job").write_text("4001", encoding="utf-8")
    (cluster.fake / "squeue.owner").write_text("somebody-else\n", encoding="utf-8")
    node, _, lines = _node(cluster, nodes, _Script(cluster, []))
    node.release("test")
    assert cluster.scancels() == []
    assert any("belongs to another user" in line for line in lines)


def test_the_cancel_script_does_nothing_when_there_is_no_hold(cluster):
    host = RemoteHost(**HOST)
    result = cluster._ssh_script(cancel_script(hold_paths(host)), check=False)
    assert result.returncode == 0 and cluster.scancels() == []


# ── reading the state ─────────────────────────────────────────────────────────────────────────────────────────────

def _state_text(job="4001", scontrol="", granted="", status="", ended=""):
    return (f"##job\n{job}\n##scontrol\n{scontrol}\n##granted\n{granted}\n##status\n{status}\n##ended\n{ended}\n")


GRANTED = "node=n1\ngpus=NVIDIA A10G\ncpus=8\ngranted_at=1000\nlimit_s=86400\njob=4001"


@pytest.mark.parametrize("text, phase", [
    (_state_text(job=""), "none"),
    (_state_text(scontrol="JobId=4001 JobState=PENDING Reason=Resources NodeList=(null)"), "queued"),
    (_state_text(scontrol="JobId=4001 JobState=CONFIGURING Reason=None NodeList=n1"), "starting"),
    (_state_text(scontrol="JobId=4001 JobState=RUNNING Reason=None NodeList=n1"), "starting"),   # script not started
    (_state_text(scontrol="JobId=4001 JobState=RUNNING Reason=None NodeList=n1", granted=GRANTED,
                 status="state=idle\nidle_s=7"), "granted"),
    (_state_text(scontrol="JobId=4001 JobState=RUNNING Reason=None NodeList=n1", granted=GRANTED,
                 status="state=busy\nidle_s=0"), "busy"),
    (_state_text(scontrol="JobId=4001 JobState=RUNNING Reason=None NodeList=n1", granted=GRANTED,
                 ended="idle 30 min"), "ended"),
    (_state_text(scontrol="JobId=4001 JobState=TIMEOUT Reason=TimeLimit NodeList=n1", granted=GRANTED), "ended"),
    (_state_text(scontrol=""), "ended"),                                            # the scheduler forgot the job
])
def test_the_phase_is_read_from_the_schedulers_state_and_the_hold_files(text, phase):
    assert parse_state(text).phase == phase


def test_the_granted_details_are_read_and_an_ended_reason_wins_over_the_schedulers_state():
    s = parse_state(_state_text(scontrol="JobId=4001 JobState=RUNNING Reason=None NodeList=n1", granted=GRANTED,
                                status="state=idle\nidle_s=7"))
    assert (s.node, s.gpus, s.cpus, s.granted_at, s.limit_s, s.idle_s, s.job_id) == (
        "n1", "NVIDIA A10G", "8", 1000, 86400, 7, "4001")
    assert s.live
    ended = parse_state(_state_text(scontrol="JobId=4001 JobState=COMPLETED Reason=None NodeList=n1",
                                    granted=GRANTED, ended="idle 30 min"))
    assert ended.ended_reason == "idle 30 min" and not ended.live
    assert parse_state(_state_text(scontrol="JobId=4001 JobState=TIMEOUT Reason=None NodeList=n1")
                       ).ended_reason == "the scheduler ended the job (TIMEOUT)"
    assert HoldState().phase == "none"


# ── the options ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_sbatch_options_keep_the_resources_and_drop_what_belongs_to_one_run():
    options, excluded, partition = sbatch_options(
        "srun --partition=gpu --gres=gpu:1 -n 4 -c 2 --mem=64G --time=4:00:00 -o run.log --pty --exclude=bad1 "
        "--exclusive --some-new-option=x")
    assert partition == "gpu" and excluded == ["bad1"]
    assert options == ["--partition=gpu", "--gres=gpu:1", "--mem=64G", "--exclusive", "--some-new-option=x",
                       "--cpus-per-task=8", "--nodes=1", "--ntasks=1"]
    assert not any(o.startswith(("--time", "-t", "-o", "--output", "--pty")) for o in options)


def test_a_scheduler_command_that_is_not_srun_cannot_request_a_node():
    with pytest.raises(HoldError, match="srun"):
        sbatch_options("sbatch --wrap=foo")


@pytest.mark.parametrize("text, seconds", [("infinite", None), ("UNLIMITED", None), ("", None), ("n/a", None),
                                           ("12:00:00", 43200), ("2-00:00:00", 172800), ("30:00", 1800)])
def test_a_partition_limit_is_seconds_or_none(text, seconds):
    assert parse_partition_limit(text) == seconds
