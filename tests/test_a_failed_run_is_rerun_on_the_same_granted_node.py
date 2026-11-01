"""The point of the whole feature, driven in the order the app runs it (tracker H14).

Request a node. Start a run: it claims the node and launches as a step of it. Make it fail. Run again: it lands on the
same node, and nothing was queued for in between. The node is never cancelled by a run ending — not by the
end-of-run close, which reads the run's sidecar and ``scancel``\\ s what it names.

Every part of this has its own tests; none of them can see the order. The claim is taken before staging, the launcher
is read at spawn, the sidecar is written by the launched command, and the close reads it afterwards — four places that
each pass in isolation and that fail together if any one of them is out of step. This runs the real
``PipelineRunner._run_pipeline_remote`` and the real ``RemoteRunner`` on a host that runs its commands for real
(``tests/_fake_remote.py``), with the stand-in scheduler of ``tests/_hold_cluster.py`` on its ``PATH``; the one thing
done by hand is what the launched command would write from inside the allocation, its ``<pidfile>.slurm``.

Wrong implementations this rejects: a run that asks for a node of its own beside the granted one (an extra ``sbatch``
or a plain ``srun --partition``); the end of a run cancelling the node (``scancel 4001``); a run that leaves the node
looking busy; and a second run that is not on the same job.
"""

from __future__ import annotations

import getpass
import re
import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from services.remote_commands import unpack_login_shell_command
from services.remote_config import RemoteHost
from services.remote_hold import node_for
from tests._fake_remote import FakeRemote
from tests._hold_cluster import Cluster, Script, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

SCHEMA = "experiment:channel:well:fov:timepoint"
WELLS = ("A01", "B02")
HOLD = "4001"


@pytest.fixture
def host():
    return RemoteHost(
        name="gpu", ssh_alias="test-host", remote_project_dir="~/allwell", remote_staging_dir="~/.allwell-staging",
        remote_python="/opt/python", cpu_only=False, extra_args=(), cache_budget_gb=10, measured_bandwidth_mbps=100.0,
        compute_launcher="srun --partition=gpu --gres=gpu:1 --cpus-per-task=8 --time=4:00:00")


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


class _Remote:
    """The host (a real filesystem, real bash), the stand-in scheduler on its PATH, and the launches recorded."""

    def __init__(self, tmp_path, monkeypatch, host, *, sidecar: str = "step"):
        from services.remote_runner import RemoteRunner
        from services.remote_upload import DatasetCache

        self.fake = FakeRemote(tmp_path / "remote-host")
        self.cluster = Cluster(tmp_path / "cluster", home=self.fake.root)
        self.fake.bin_dir = self.cluster.bin
        self.commands: list[str] = []
        self.exit_codes = [1, 0]          # the first run fails, the rerun succeeds
        self.steps = 0
        #: What a step's stream says, and what happens on the host while it runs (both for the step launch only).
        self.step_output: list[str] = []
        self.during_step = None

        def launched(argv):
            script = unpack_login_shell_command(argv[-1])
            self.commands.append(script)
            match = re.match(r'echo \$\$ > "\$HOME"(\S+?);', script)
            if match and "--jobid" in script:
                # What the launched command writes from inside the allocation: `<job>.<step>` in the sidecar.
                self.steps += 1
                recorded = f"{HOLD}.{self.steps + 2}" if sidecar == "step" else HOLD      # "bare": an inner script that
                self.fake.path("~" + match.group(1) + ".slurm").write_text(recorded + "\n")   # wrote $SLURM_JOB_ID only
                self.cluster.add_step(f"{HOLD}.{self.steps + 2}")
                if self.during_step is not None:
                    self.during_step()
            proc = MagicMock()
            proc.stdout = iter(list(self.step_output) if "--jobid" in script else [])
            # A job queued early, in a directory of its own, is not a run: it must not use up the scripted failure.
            code = 0 if "/reservations/" in script else (self.exit_codes.pop(0) if self.exit_codes else 0)
            proc.wait.return_value = code
            proc.poll.return_value = code
            proc.returncode = code
            proc.pid = 4242
            return proc

        self.fake.install(monkeypatch, host, on_launch=launched)
        monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                            lambda self, zipped, **_k: (self.cache_key(zipped), 0, 0.01))
        monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)

    def request_the_node(self, host):
        """Manage Remotes' Request a node, through the real `GrantedNode` and the real scripts."""
        from services.remote_upload import DatasetCache

        def granted():
            self.cluster.set_job(int(HOLD), "RUNNING", "None", "gpu-node-1")
            granted_files(self.cluster, host, job=int(HOLD))

        (self.cluster.fake / "next_id").write_text(f"{HOLD}\n", encoding="utf-8")
        sleep = Script(self.cluster, [granted])
        node = node_for(host, DatasetCache(host), log_fn=lambda _m: None, sleep=sleep, clock=sleep.clock)
        node.request()
        return node


def _run_once(plate, host, stop_fetchers):
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import RemoteRunner

    inp, out = plate
    r = pr.PipelineRunner()
    r._remote = host
    r._remote_runner = RemoteRunner(host)
    stop_fetchers(r._remote_runner)
    r._journal_spec = {"opts": {}}
    opts = {"filename_schema": SCHEMA, "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", "force": True}
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._open_journal(inp, out, args, opts)
    r._run_pipeline_remote(inp, out, args, opts)
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return r, "".join(str(e[1]) for e in events if e[0] in ("line", "done", "error"))


@pytest.fixture(autouse=True)
def _fresh_registry():
    from services import remote_hold

    remote_hold._NODES.clear()
    yield
    for node in remote_hold.live_nodes():
        node.stop_beating()
    remote_hold._NODES.clear()


def test_a_run_that_fails_leaves_the_node_and_the_rerun_lands_on_it(tmp_path, monkeypatch, plate, host, stop_fetchers):
    remote = _Remote(tmp_path, monkeypatch, host)
    node = remote.request_the_node(host)
    cluster = remote.cluster
    assert len(cluster.sbatch_calls()) == 1, "the request is the one allocation this whole sequence makes"
    busy = cluster.hold_dir(host) / "busy"
    me = getpass.getuser()

    # ── run 1: claims the node, launches as a step of it, fails ────────────────────────────────────────────────
    r1, text1 = _run_once(plate, host, stop_fetchers)
    launches = [c for c in remote.commands if "/reservations/" not in c]
    assert not [c for c in remote.commands if "/reservations/" in c], (
        "an early request for a node of the run's own was made beside the granted one")
    assert len(launches) == 1 and launches[0].startswith('echo $$ > "$HOME"/.allwell-staging/'), remote.commands
    assert f"exec srun --jobid={HOLD} --overlap --nodes=1 --ntasks=1 --cpus-per-task=8 --gres=gpu:1 bash -lc" in \
        launches[0], launches[0][:200]
    assert "--partition=gpu" not in launches[0] and "sinfo" not in launches[0]
    assert "using gpu-node-1" in text1 and "running on the granted node" in text1
    assert text1.index("using gpu-node-1") < text1.index("scanning input"), (
        "the node must be claimed before anything is staged, or the idle clock runs while a plate is prepared")
    assert cluster.scancels() == [f"-u {me} {HOLD}.3"], (
        f"the end of the run must cancel its step and only that: {cluster.scancels()}\n{text1}")
    assert (cluster.fake / "jobs" / HOLD).read_text().startswith("RUNNING"), "the failed run took the node with it"
    assert not busy.exists(), "the run is over and the node still reads as in use"
    assert "exited with code 1" in text1, text1

    # ── run 2: the same plate again, on the same node, nothing queued in between ───────────────────────────────
    r2, text2 = _run_once(plate, host, stop_fetchers)
    launches = [c for c in remote.commands if "/reservations/" not in c]
    assert len(launches) == 2
    assert f"exec srun --jobid={HOLD} --overlap" in launches[1]
    assert len(cluster.sbatch_calls()) == 1, "a second node was requested"
    assert cluster.scancels() == [f"-u {me} {HOLD}.3", f"-u {me} {HOLD}.4"]
    assert (cluster.fake / "jobs" / HOLD).read_text().startswith("RUNNING")
    assert not busy.exists()
    assert not [c for c in remote.commands if "/reservations/" in c]
    assert "exited with code" not in text2, text2
    assert node.state().phase in ("granted", "busy") and node.state().job_id == HOLD


def test_the_same_runs_without_a_granted_node_ask_for_one_each_time(tmp_path, monkeypatch, plate, host, stop_fetchers):
    """The control: nothing here is a property of the harness. With no node granted each run is an ordinary one, with
    the host's own launcher, and writes the bare job id."""
    remote = _Remote(tmp_path, monkeypatch, host)
    r, text = _run_once(plate, host, stop_fetchers)
    launched = [c for c in remote.commands if "/reservations/" not in c]
    assert launched and "--jobid" not in launched[0] and "--partition=gpu" in launched[0]
    assert remote.cluster.sbatch_calls() == []


def test_a_launched_command_that_recorded_only_the_job_id_still_cannot_cancel_the_node(
        tmp_path, monkeypatch, plate, host, stop_fetchers):
    """The defence under the step form: if anything ever records the bare job id of the granted node in a run's sidecar
    (an older inner script, a hand edit), the end of the run must leave the node alone, not `scancel 4001`."""
    remote = _Remote(tmp_path, monkeypatch, host, sidecar="bare")
    remote.request_the_node(host)
    r, text = _run_once(plate, host, stop_fetchers)
    assert remote.cluster.scancels() == [], f"the end of a run cancelled the granted node:\n{text}"
    assert (remote.cluster.fake / "jobs" / HOLD).read_text().startswith("RUNNING")
    assert "the granted node's own job" in text and "left granted" in text
    assert not (remote.cluster.hold_dir(host) / "busy").exists()


def test_a_step_ended_by_the_node_releasing_itself_is_not_blamed_on_an_scancel(
        tmp_path, monkeypatch, plate, host, stop_fetchers):
    """The reported case: the node's script stopped hearing from the app, released the node, and Slurm cancelled the
    run's step with it — slurmstepd's line names no cause. The headline said "cancelled outside this app (an scancel by
    you or an administrator)", which nobody had done.

    Wrong implementations this rejects: reading the cancellation from slurmstepd's line alone (the scancel wording);
    asking the node after the run has released its claim (the lease is gone, so nothing is asked); and naming the
    release without the reason the node wrote."""
    remote = _Remote(tmp_path, monkeypatch, host)
    remote.request_the_node(host)
    hold_dir = remote.cluster.hold_dir(host)
    reason = ("All-Well stopped answering (2 min without a heartbeat) and the run using it had not been heard from "
              "for 10 min either")

    def node_releases_itself():
        (hold_dir / "ended").write_text(reason + "\n", encoding="utf-8")
        remote.cluster.set_job(int(HOLD), "COMPLETED", "None", "gpu-node-1")

    remote.during_step = node_releases_itself
    remote.step_output = [
        "srun: Job step aborted: Waiting up to 32 seconds for job step to finish.\n",
        f"slurmstepd: error: *** STEP {HOLD}.3 ON gpu-node-1 CANCELLED AT 2026-11-01T10:00:00 ***\n",
    ]
    r, text = _run_once(plate, host, stop_fetchers)
    errors = [e for e in text.splitlines() if "was terminated" in e]
    assert errors, text
    headline = errors[-1]
    assert "scancel by you or an administrator" not in headline, headline
    assert f"the granted node gpu-node-1 ended, and step {HOLD}.3 with it: {reason}" in headline, headline
    assert "slept" in headline, "the likeliest cause of the app going quiet is not named"


def test_a_step_cancelled_while_the_node_is_still_granted_is_still_an_scancel(
        tmp_path, monkeypatch, plate, host, stop_fetchers):
    """The control: the node is alive, so whoever cancelled the step did it from outside, and the old words stand."""
    remote = _Remote(tmp_path, monkeypatch, host)
    remote.request_the_node(host)
    remote.step_output = [
        f"slurmstepd: error: *** STEP {HOLD}.3 ON gpu-node-1 CANCELLED AT 2026-11-01T10:00:00 ***\n",
    ]
    r, text = _run_once(plate, host, stop_fetchers)
    assert "scancel by you or an administrator" in text, text
    assert "the granted node gpu-node-1 ended" not in text
