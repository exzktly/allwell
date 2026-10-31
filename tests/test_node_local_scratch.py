"""A Slurm job's scratch goes to the node's own disk, not the cluster's shared home folder.

The report: on AWS ParallelCluster a nanowell run's registration, with ten workers on a 4 x L4 node and again with
three on an A10G node, stalled the app's run heartbeat — "did not answer within 75s", three times, and the warning
that the node would be given back. The run's scratch was on ``~/.allwell-staging`` / ``~/.allwell/scratch``: the
home folder, which every node reads over the network from the head node the app's ssh and heartbeat also use.

``pipeline.node_disk.node_local_scratch`` picks ``/scratch`` (the node's NVMe drive on ParallelCluster), then
``$SLURM_TMPDIR``; never ``$TMPDIR`` or the system temporary directory, since without ``--exclusive`` the node's
boot disk is shared with other people's jobs. ``pipeline.io.scratch_base_for`` uses it inside a Slurm job when no
scratch folder was set, decides once, and pins the answer in ``ALLWELL_SCRATCH_DIR`` for every worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import io as pio
from pipeline import node_disk as nd

GB = 2 ** 30


@pytest.fixture
def disks(tmp_path, monkeypatch):
    """Three folders standing in for /scratch, $SLURM_TMPDIR and /tmp, each on a filesystem type of the test's
    choosing, with the free space the test gives it."""
    folders = {name: tmp_path / name for name in ("scratch", "slurm_tmp", "tmp")}
    for folder in folders.values():
        folder.mkdir()
    kinds = {folder: "xfs" for folder in folders.values()}
    free = {folder: 500 * GB for folder in folders.values()}
    monkeypatch.setattr(nd, "NODE_SCRATCH_MOUNT", str(folders["scratch"]))
    monkeypatch.setattr(nd, "filesystem_type", lambda path, mounts="/proc/mounts": kinds.get(Path(path), "xfs"))
    return folders, kinds, free


def _pick(folders, free, **environ):
    environ.setdefault("SLURM_JOB_ID", "4812345")
    return nd.node_local_scratch(environ, free_bytes=lambda p: free[Path(p)])


def test_scratch_is_the_first_choice_even_when_the_site_sets_a_job_tmpdir(disks):
    folders, _kinds, free = disks
    folder, reason = _pick(folders, free, SLURM_TMPDIR=str(folders["slurm_tmp"]), TMPDIR=str(folders["tmp"]))
    assert folder == folders["scratch"] / f"allwell-{nd._user()}" and folder.is_dir()
    assert reason == f"this node's own disk {folders['scratch']} (500 GB free)"


def test_slurm_tmpdir_is_taken_when_there_is_no_usable_scratch(disks):
    folders, kinds, free = disks
    kinds[folders["scratch"]] = "nfs4"                     # a site that mounts a shared filesystem at /scratch
    folder, _ = _pick(folders, free, SLURM_TMPDIR=str(folders["slurm_tmp"]))
    assert folder == folders["slurm_tmp"] / f"allwell-{nd._user()}"


@pytest.mark.parametrize("what,why", [
    ("nfs", "is on a network filesystem (nfs)"),
    ("tmpfs", "is held in memory (tmpfs)"),
    ("full", "has 3.0 GB free, under 20 GB"),
    ("missing", "does not exist"),
])
def test_tmpdir_and_the_system_temp_folder_are_never_taken_and_the_reason_names_what_each_lacked(disks, what, why):
    """``$TMPDIR`` here is the node's boot disk, as a login environment often sets it: never a run's scratch, on a
    node other jobs may share."""
    folders, kinds, free = disks
    scratch = folders["scratch"]
    if what == "full":
        free[scratch] = 3 * GB
    elif what == "missing":
        scratch.rmdir()
    else:
        kinds[scratch] = what
    folder, reason = _pick(folders, free, TMPDIR=str(folders["tmp"]))
    assert folder is None
    assert reason == f"no node-local disk qualifies: {scratch} {why}"
    assert not (folders["tmp"] / f"allwell-{nd._user()}").exists()


def test_outside_a_slurm_job_nothing_is_chosen(disks):
    folders, _kinds, free = disks
    assert nd.node_local_scratch({}, free_bytes=lambda p: free[Path(p)]) == (None, "")
    assert not list(folders["scratch"].iterdir())


@pytest.fixture
def fresh_decision(monkeypatch):
    monkeypatch.setattr(pio, "_NODE_LOCAL_DECIDED", False)
    monkeypatch.delenv(pio.SCRATCH_DIR_ENV, raising=False)


def test_a_slurm_job_with_no_scratch_folder_set_runs_on_the_node_and_its_workers_inherit_it(
        disks, fresh_decision, monkeypatch, tmp_path):
    folders, _kinds, free = disks
    monkeypatch.setenv("SLURM_JOB_ID", "4812345")
    monkeypatch.setattr(nd.shutil, "disk_usage", lambda p: type("U", (), {"free": free[Path(p)]})())
    out = tmp_path / "out"
    first = pio.scratch_base_for(out)
    assert first == folders["scratch"] / f"allwell-{nd._user()}"
    import os
    assert os.environ[pio.SCRATCH_DIR_ENV] == str(first), "a worker the run starts would not inherit the choice"
    free[folders["scratch"]] = 1 * GB                       # the disk fills as the run works
    assert pio.scratch_base_for(out) == first, "the run moved its scratch to another disk part-way through"


def test_a_scratch_folder_the_user_set_wins(disks, fresh_decision, monkeypatch, tmp_path):
    monkeypatch.setenv("SLURM_JOB_ID", "4812345")
    chosen = tmp_path / "chosen"
    monkeypatch.setenv(pio.SCRATCH_DIR_ENV, str(chosen))
    assert pio.scratch_base_for(tmp_path / "out") == chosen
    folders, _kinds, _free = disks
    assert not list(folders["scratch"].iterdir()), "the node's disk was used although a folder was set"


def test_with_no_node_local_disk_the_shared_default_stays(disks, fresh_decision, monkeypatch, tmp_path):
    folders, kinds, free = disks
    kinds[folders["scratch"]] = "nfs4"
    monkeypatch.setenv("SLURM_JOB_ID", "4812345")
    monkeypatch.setattr(nd.shutil, "disk_usage", lambda p: type("U", (), {"free": free[Path(p)]})())
    home = tmp_path / "home-scratch"
    monkeypatch.setattr(pio, "default_scratch_base", lambda: home)
    assert pio.scratch_base_for(tmp_path / "out") == home
    import os
    assert pio.SCRATCH_DIR_ENV not in os.environ


# ── through the remote command the app actually sends ─────────────────────────────────────────────────────────────

def _remote_run_scratch(tmp_path, monkeypatch, *, host_scratch: str = "", slurm: bool = True) -> tuple[Path, Path]:
    """The scratch a remote run's pipeline ends up with, through the app's own path: the argv
    ``RemoteRunner._build_remote_argv`` builds, the host's scratch folder exported as the composed command exports it
    (``remote_commands._remote_scratch_export``), the pipeline's real parser, ``publish_scratch_args`` as
    ``process_microscopy.main`` calls it, then ``scratch_base_for``.

    Testing ``node_local_scratch`` alone passed while no remote run could reach it: the remote argv always carried
    ``--scratch_dir=<run output>``, which outranked the node's disk and overwrote the host's exported folder."""
    import re
    import shlex

    from pipeline.cli_args import build_parser
    from services.remote_commands import _remote_scratch_export
    from services.remote_config import RemoteHost
    from services.remote_runner import RemoteRunner
    from services.remote_run_state import LocalInputMode, OutputFilter

    host = RemoteHost(name="aws", ssh_alias="aws", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.allwell-staging", compute_launcher="srun -p gpu-large-queue",
                      remote_scratch_dir=host_scratch)
    remote_out = tmp_path / "staging" / "runs" / "run-1" / "out"
    local = ["/usr/bin/python", "/local/process_microscopy.py", "--input_dir", "/x", "--output_dir", "/y"]
    argv = RemoteRunner(host)._build_remote_argv(local, "/r/in", str(remote_out), OutputFilter.FULL,
                                                 LocalInputMode.ZIP)
    export = _remote_scratch_export(host.remote_scratch_dir)
    if export:
        monkeypatch.setenv(pio.SCRATCH_DIR_ENV, re.search(r'ALLWELL_SCRATCH_DIR="([^"]*)"', export).group(1))
    if slurm:
        monkeypatch.setenv("SLURM_JOB_ID", "4812345")
    else:
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    pipeline_args = argv[argv.index("python") + 2:]           # after `… python <remote>/process_microscopy.py`
    args = build_parser().parse_args(shlex.split(shlex.join(pipeline_args)))
    pio.publish_scratch_args(args.scratch_dir, args.scratch_fallback_dir)
    return pio.scratch_base_for(Path(args.output_dir)), remote_out


@pytest.fixture
def remote_env(disks, fresh_decision, monkeypatch):
    folders, _kinds, free = disks
    # ``publish_scratch_args`` writes both variables into ``os.environ`` directly. A bare ``delenv`` of an unset
    # variable records no undo, so the run's fallback outlived the test and became the next test's scratch
    # (``test_scratch_base`` read ``staging/runs/run-1/out``). Setting each first makes monkeypatch restore it.
    for name in (pio.SCRATCH_FALLBACK_ENV, pio.SCRATCH_DIR_ENV):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setattr(nd.shutil, "disk_usage", lambda p: type("U", (), {"free": free[Path(p)]})())
    return disks


def test_a_remote_slurm_run_with_no_scratch_folder_set_uses_the_node_s_disk(remote_env, monkeypatch, tmp_path):
    folders, _kinds, _free = remote_env
    scratch, _out = _remote_run_scratch(tmp_path, monkeypatch)
    assert scratch == folders["scratch"] / f"allwell-{nd._user()}"


def test_a_remote_run_s_host_scratch_folder_is_used(remote_env, monkeypatch, tmp_path):
    chosen = tmp_path / "chosen-scratch"
    scratch, _out = _remote_run_scratch(tmp_path, monkeypatch, host_scratch=str(chosen))
    assert scratch == chosen


def test_a_remote_run_with_no_node_disk_keeps_its_scratch_in_the_run_s_output_folder(remote_env, monkeypatch,
                                                                                       tmp_path):
    folders, kinds, _free = remote_env
    kinds[folders["scratch"]] = "nfs4"
    scratch, out = _remote_run_scratch(tmp_path, monkeypatch)
    assert scratch == out, "without a node disk the scratch left the staging area for the home folder's quota"
    scratch_off_slurm, out = _remote_run_scratch(tmp_path, monkeypatch, slurm=False)
    assert scratch_off_slurm == out
