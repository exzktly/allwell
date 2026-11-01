"""A run started while the requested node is still queued prepares and uploads the plate while it waits.

The report: a node was requested from Manage Remotes and was still queued; an Analyze run started then sat on
"the granted node is still being granted; waiting for it" and did not stage or upload anything. The claim waited for
the grant before staging, so the queue wait and the whole plate's preparation and upload ran end to end — where a run
with a job of its own overlaps them (``services.remote_reservation``).

What replaces it: the run claims the node at once — the ``busy`` marker needs only the hold's directory, and the hold
script reads it on its first look, so the node starts busy and its idle clock never runs during the upload
(``test_remote_hold_script.py::test_a_node_claimed_while_it_was_queued_starts_busy_and_its_idle_clock_never_runs``) —
and waits for the grant only at launch (``GrantedRun.await_node``): single-shot after the upload, batched after
batch 1's.

Wrong implementations these reject: waiting before staging (the report); launching with the host's own ``srun``
while the claimed node is still queued, which asks for a second node beside it; a hold that ended while queued
leaving the run with no node or with the marker still set; giving up on a node that failed to boot while Manage
Remotes' request asks for another; and a wait that ignores Stop or the host's Max queue wait.
"""

from __future__ import annotations

import re
import shutil
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from services.remote_commands import unpack_login_shell_command
from services.remote_config import RemoteHost
from services.remote_hold import GrantedRun, HoldError, HoldQueueWaitSpent
from tests._hold_cluster import HOST, Cluster, Script, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def cluster(tmp_path):
    return Cluster(tmp_path)


@pytest.fixture(autouse=True)
def _fresh_registry():
    from services import remote_hold

    remote_hold._NODES.clear()
    yield
    for node in remote_hold.live_nodes():
        node.stop_beating()
    remote_hold._NODES.clear()


def _queued(cluster, host, job=4001):
    """A hold job the scheduler has not started: the directory and its job id, nothing granted."""
    cluster.set_job(job, "PENDING", "Resources", "")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job").write_text(str(job), encoding="utf-8")
    return d


def _grant(cluster, host, job=4001):
    cluster.set_job(job, "RUNNING", "None", "gpu-node-1")
    granted_files(cluster, host, job=job)


def _run(cluster, host, sleep, lines, stop=None):
    from services.remote_runner import RemoteRunner

    runner = RemoteRunner(host)
    runner._cache = cluster
    run = GrantedRun(runner, batch_size=0, nanowell=False, log_fn=lines.append, stop_event=stop or threading.Event(),
                     sleep=sleep, clock=sleep.clock)
    return runner, run


# ── the claim and the wait, over the real scripts ────────────────────────────────────────────────────────────────

def test_a_queued_node_is_claimed_at_once_and_waited_for_only_at_launch(cluster):
    host = RemoteHost(**HOST)
    d = _queued(cluster, host)
    lines: list[str] = []
    sleep = Script(cluster, [lambda: _grant(cluster, host)])
    runner, run = _run(cluster, host, sleep, lines)
    run.claim()
    assert sleep.naps == [], "the claim waited for the grant, so nothing is staged or uploaded meanwhile"
    assert run.active and (d / "busy").exists()
    assert runner.node_pending() and runner.step_target is None
    with pytest.raises(HoldError, match="still being granted"):
        runner.launcher()           # the host's own srun here would ask for a second node beside the claimed one
    run.check_fit(10)               # the well count arrives while queued: nothing to compare yet, nothing replaced
    assert cluster.sbatch_calls() == []

    runner.await_granted_node()
    assert len(sleep.naps) == 1 and not runner.node_pending()
    assert runner.launcher().startswith("srun --jobid=4001 --overlap"), runner.launcher()
    assert any("waiting for the granted node (job 4001) to be granted" in line for line in lines), lines
    assert any("using gpu-node-1 (NVIDIA A10G), job 4001" in line for line in lines), lines
    run.release()
    assert not (d / "busy").exists() and runner.step_target is None


def test_a_hold_that_ends_while_queued_gives_the_claim_back_and_the_run_asks_for_its_own(cluster):
    host = RemoteHost(**HOST)
    d = _queued(cluster, host)
    lines: list[str] = []
    sleep = Script(cluster, [lambda: cluster.set_job(4001, "CANCELLED", "None", "")])
    runner, run = _run(cluster, host, sleep, lines)
    run.claim()
    runner.await_granted_node()
    assert not run.active and runner.step_target is None and not runner.node_pending()
    assert not (d / "busy").exists(), "a claim on a node that will never run must not be left behind"
    assert runner.launcher() == host.launch_command
    assert any("ended before it was granted" in line and "node of its own" in line for line in lines), lines


def test_a_node_that_failed_to_boot_is_still_waited_for_while_another_is_asked_for(cluster):
    """Manage Remotes' request resubmits into the same directory after a boot failure; the run's marker stays there.
    Wrong implementation: reading the BOOT_FAIL as the hold having ended, which sends the run off to ask for a node of
    its own beside the one being retried."""
    host = RemoteHost(**HOST)
    d = _queued(cluster, host)
    lines: list[str] = []

    def resubmitted():
        (d / "job").write_text("4002", encoding="utf-8")
        cluster.set_job(4002, "PENDING", "Resources", "")

    sleep = Script(cluster, [lambda: cluster.set_job(4001, "BOOT_FAIL", "None", "gpu-node-1"),
                             resubmitted, lambda: _grant(cluster, host, job=4002)])
    runner, run = _run(cluster, host, sleep, lines)
    run.claim()
    runner.await_granted_node()
    assert run.active and runner.step_target.job_id == "4002", lines
    assert (d / "busy").exists()
    assert not any("node of its own" in line for line in lines), lines
    run.release()


def test_stop_ends_the_wait_at_launch(cluster):
    host = RemoteHost(**HOST)
    _queued(cluster, host)
    stop = threading.Event()
    sleep = Script(cluster, [stop.set])
    runner, run = _run(cluster, host, sleep, [], stop=stop)
    run.claim()
    with pytest.raises(HoldError, match="stopped"):
        runner.await_granted_node()
    run.release()


def test_the_max_queue_wait_counts_from_the_claim(cluster):
    host = RemoteHost(**{**HOST, "queue_wait_timeout_min": 1})
    _queued(cluster, host)
    sleep = Script(cluster, [])
    runner, run = _run(cluster, host, sleep, [])
    run.claim()
    sleep.now += 50                 # the upload took 50 s of the minute
    with pytest.raises(HoldQueueWaitSpent):
        runner.await_granted_node()
    assert len(sleep.naps) == 1, "the wait must count the time already spent uploading, not start a fresh minute"
    assert cluster.sbatch_calls() == []
    run.release()


# ── the order, through the real PipelineRunner ─────────────────────────────────────────────────────────────────

SCHEMA = "experiment:channel:well:fov:timepoint"
WELLS = ("A01", "B02")
HOLD = "4001"


@pytest.fixture
def host():
    return RemoteHost(
        name="gpu", ssh_alias="test-host", remote_project_dir="~/allwell", remote_staging_dir="~/.allwell-staging",
        remote_python="/opt/python", cpu_only=False, extra_args=(), cache_budget_gb=10, measured_bandwidth_mbps=100.0,
        compute_launcher="srun --partition=gpu --gres=gpu:1 --cpus-per-task=8 --time=4:00:00",
        queue_wait_timeout_min=1)


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
    """A host that runs its commands for real, the stand-in scheduler on its PATH, and a record of what happened in
    which order: each upload (with the hold job's state at that moment) and each launch."""

    def __init__(self, tmp_path, monkeypatch, host):
        from services.remote_runner import RemoteRunner
        from services.remote_upload import DatasetCache
        from tests._fake_remote import FakeRemote

        self.fake = FakeRemote(tmp_path / "remote-host")
        self.cluster = Cluster(tmp_path / "cluster", home=self.fake.root)
        self.fake.bin_dir = self.cluster.bin
        self.events: list[tuple[str, str]] = []
        self.host = host

        def launched(argv):
            script = unpack_login_shell_command(argv[-1])
            self.events.append(("launch", script))
            match = re.match(r'echo \$\$ > "\$HOME"(\S+?);', script)
            if match and "--jobid" in script:
                self.fake.path("~" + match.group(1) + ".slurm").write_text(f"{HOLD}.3\n")
                self.cluster.add_step(f"{HOLD}.3")
            proc = MagicMock()
            proc.stdout = iter([])
            proc.wait.return_value = proc.poll.return_value = proc.returncode = 0
            proc.pid = 4242
            return proc

        self.fake.install(monkeypatch, host, on_launch=launched)
        remote = self

        def ensure_uploaded(cache, zipped, **_k):
            remote.uploaded()
            return cache.cache_key(zipped), 0, 0.01

        def upload_subset(cache, zips, dest, **_kw):
            target = remote.fake.path(dest)          # what rsync would have put there
            target.mkdir(parents=True, exist_ok=True)
            for z in zips:
                shutil.copy2(z, target / Path(z).name)
            remote.uploaded()
            return sum(Path(z).stat().st_size for z in zips), 0.01, 0

        monkeypatch.setattr(DatasetCache, "ensure_uploaded", ensure_uploaded)
        monkeypatch.setattr(DatasetCache, "upload_subset", upload_subset)
        monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)

    def uploaded(self):
        """An upload finished: note the hold's state then, and let the scheduler grant the node at this moment."""
        jobs = self.cluster.fake / "jobs" / HOLD
        self.events.append(("upload", jobs.read_text().split("|")[0] if jobs.exists() else "?"))
        _grant(self.cluster, self.host, job=int(HOLD))


def _run_once(plate, host, stop_fetchers, **extra):
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
            "csv_prefix": "measurements", "force": True, **extra}
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._open_journal(inp, out, args, opts)
    # On a thread with a deadline: a run whose launch never comes (a batched driver left unlaunched) waits for its
    # batch for ever, and pytest's timeout cannot always interrupt that. Stop ends the wait; the test then fails.
    worker = threading.Thread(target=r._run_pipeline_remote, args=(inp, out, args, opts), daemon=True)
    worker.start()
    worker.join(60)
    hung = worker.is_alive()
    if hung:
        r._stop_requested.set()
        worker.join(30)
    assert not hung, "the run did not finish within 60 s: it never launched on the node"
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return "".join(str(e[1]) for e in events if e[0] in ("line", "done", "error"))


@pytest.mark.parametrize("batch", [0, 1], ids=["single-shot", "batched"])
def test_the_plate_goes_up_while_the_node_queues_and_launches_as_a_step_once_granted(
        tmp_path, monkeypatch, plate, host, stop_fetchers, batch):
    remote = _Remote(tmp_path, monkeypatch, host)
    _queued(remote.cluster, host, job=int(HOLD))
    text = _run_once(plate, host, stop_fetchers, remote_batch_size=batch)

    kinds = [kind for kind, _ in remote.events]
    assert "upload" in kinds and "launch" in kinds, (remote.events, text)
    first_upload, first_launch = kinds.index("upload"), kinds.index("launch")
    assert first_upload < first_launch, f"the launch waited for nothing and the upload came after it: {kinds}"
    assert remote.events[first_upload][1] == "PENDING", "the upload must happen while the node is still queued"
    launch = remote.events[first_launch][1]
    assert f"srun --jobid={HOLD} --overlap" in launch, launch[:300]
    assert "--partition=gpu" not in launch, "a node of the run's own was asked for beside the claimed one"
    assert not [s for k, s in remote.events if k == "launch" and "/reservations/" in s]
    assert remote.cluster.sbatch_calls() == []
    assert text.index("this run has claimed it") < text.index("scanning input"), text
    assert not (remote.cluster.hold_dir(host) / "busy").exists(), "the run is over and the node still reads as in use"
