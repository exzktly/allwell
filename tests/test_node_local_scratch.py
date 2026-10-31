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
