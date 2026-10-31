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
def rooted():
    """The folders the ``disks`` fixture puts on the node's root filesystem; a test adds to it. Empty by default,
    because a test's temporary folders really are on this machine's root filesystem wherever ``/tmp`` is not a
    mount of its own, and ``/scratch`` there is refused (``test_a_scratch_folder_on_the_boot_disk_is_refused…``)."""
    return set()


@pytest.fixture
def disks(tmp_path, monkeypatch, rooted):
    """Three folders standing in for /scratch, $SLURM_TMPDIR and /tmp, each on a filesystem type of the test's
    choosing, with the free space the test gives it, and on the root filesystem when the test puts it in ``rooted``."""
    folders = {name: tmp_path / name for name in ("scratch", "slurm_tmp", "tmp")}
    for folder in folders.values():
        folder.mkdir()
    kinds = {folder: "xfs" for folder in folders.values()}
    free = {folder: 500 * GB for folder in folders.values()}
    monkeypatch.setattr(nd, "NODE_SCRATCH_MOUNT", str(folders["scratch"]))
    monkeypatch.setattr(nd, "filesystem_type", lambda path, mounts="/proc/mounts": kinds.get(Path(path), "xfs"))
    monkeypatch.setattr(nd, "on_root_filesystem", lambda path: Path(path) in rooted)
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


def test_a_scratch_folder_on_the_boot_disk_is_refused_and_slurm_tmpdir_is_taken(disks, rooted):
    """``/scratch`` qualified by its filesystem type alone, so a plain folder on ``/`` (xfs, local, writable, roomy)
    took the run's scratch: the node's boot disk, which a run must never fill on a node it may share."""
    folders, _kinds, free = disks
    rooted.add(folders["scratch"])
    folder, reason = _pick(folders, free)
    assert (folder, reason) == (None, f"no node-local disk qualifies: {folders['scratch']} is a folder on the node's "
                                      "root filesystem, not a disk of its own")
    assert not list(folders["scratch"].iterdir())
    folder, _ = _pick(folders, free, SLURM_TMPDIR=str(folders["slurm_tmp"]))
    assert folder == folders["slurm_tmp"] / f"allwell-{nd._user()}"
    rooted.add(folders["slurm_tmp"])           # the site's own job folder is where the site put it
    folder, _ = _pick(folders, free, SLURM_TMPDIR=str(folders["slurm_tmp"]))
    assert folder == folders["slurm_tmp"] / f"allwell-{nd._user()}"


def test_the_root_filesystem_is_told_by_device_not_by_the_mount_table():
    """The real check, unpatched: ``/`` is on itself and ``/proc`` (a mount of its own on any Linux) is not."""
    import os
    assert nd.on_root_filesystem("/") is True
    if not os.path.ismount("/proc"):
        pytest.skip("no /proc mount on this platform")
    assert nd.on_root_filesystem("/proc") is False


def test_a_plain_folder_on_this_machine_s_root_filesystem_is_refused_through_the_real_check(tmp_path, monkeypatch):
    """The reviewer's probe: a mount table that says local xfs, a ``/scratch`` that is only a folder, and the device
    check left as it ships."""
    import os
    if os.stat(tmp_path).st_dev != os.stat(os.sep).st_dev:
        pytest.skip("the temporary folder is a filesystem of its own here")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(nd, "NODE_SCRATCH_MOUNT", str(scratch))
    monkeypatch.setattr(nd, "filesystem_type", lambda path, mounts="/proc/mounts": "xfs")
    assert nd.node_local_scratch({"SLURM_JOB_ID": "7"}, free_bytes=lambda p: 30 * GB) == (
        None, f"no node-local disk qualifies: {scratch} is a folder on the node's root filesystem, not a disk of its own")


def test_outside_a_slurm_job_nothing_is_chosen(disks):
    folders, _kinds, free = disks
    assert nd.node_local_scratch({}, free_bytes=lambda p: free[Path(p)]) == (None, "")
    assert not list(folders["scratch"].iterdir())


@pytest.fixture
def fresh_decision(monkeypatch):
    monkeypatch.setattr(pio, "_NODE_LOCAL_DECIDED", False)
    monkeypatch.setattr(pio, "_NODE_LOCAL_FOLDER", None)
    monkeypatch.delenv(pio.SCRATCH_DIR_ENV, raising=False)
    # Set first so monkeypatch records an undo: the decision writes it into ``os.environ`` directly.
    monkeypatch.setenv(pio.NODE_SCRATCH_DECLINED_ENV, "")
    monkeypatch.delenv(pio.NODE_SCRATCH_DECLINED_ENV)


def _as_a_spawned_worker(monkeypatch):
    """What a worker the run spawns starts with: this module imported afresh, so nothing decided yet, and the
    parent's ``os.environ`` inherited as it stood."""
    monkeypatch.setattr(pio, "_NODE_LOCAL_DECIDED", False)
    monkeypatch.setattr(pio, "_NODE_LOCAL_FOLDER", None)


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


def test_a_worker_keeps_the_parent_s_no_node_disk_answer_after_the_disk_frees_up(
        disks, fresh_decision, monkeypatch, tmp_path):
    """The parent found ``/scratch`` with 19 GB free and kept the shared default; another job then freed space, and
    each worker, deciding afresh, took ``/scratch``. One run, two scratch folders: the parent's sweep and the
    workers' staging no longer agreed on where a well's working files were."""
    folders, _kinds, free = disks
    free[folders["scratch"]] = 19 * GB
    monkeypatch.setenv("SLURM_JOB_ID", "4812345")
    monkeypatch.setattr(nd.shutil, "disk_usage", lambda p: type("U", (), {"free": free[Path(p)]})())
    home = tmp_path / "home-scratch"
    monkeypatch.setattr(pio, "default_scratch_base", lambda: home)
    assert pio.scratch_base_for(tmp_path / "out") == home
    free[folders["scratch"]] = 25 * GB
    _as_a_spawned_worker(monkeypatch)
    assert pio.scratch_base_for(tmp_path / "out") == home
    assert not list(folders["scratch"].iterdir())


def test_a_worker_takes_the_parent_s_node_disk(disks, fresh_decision, monkeypatch, tmp_path):
    folders, _kinds, free = disks
    monkeypatch.setenv("SLURM_JOB_ID", "4812345")
    monkeypatch.setattr(nd.shutil, "disk_usage", lambda p: type("U", (), {"free": free[Path(p)]})())
    first = pio.scratch_base_for(tmp_path / "out")
    free[folders["scratch"]] = 1 * GB
    _as_a_spawned_worker(monkeypatch)
    assert pio.scratch_base_for(tmp_path / "out") == first == folders["scratch"] / f"allwell-{nd._user()}"


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
    for name in (pio.SCRATCH_FALLBACK_ENV, pio.SCRATCH_DIR_ENV, pio.NODE_SCRATCH_DECLINED_ENV):
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


# ── the run's own estimate, once it exists ────────────────────────────────────────────────────────────────────────

MiB = 2 ** 20


def _preflight_remote_run(tmp_path, monkeypatch, caplog, *, free, host_scratch=""):
    """A remote Slurm run's scratch decision, then the scratch preflight as ``process_microscopy.main`` calls it
    (``pipeline.io.preflight_run_scratch``), against a plate whose estimate the real ``sample_image_bytes`` reads:
    1 MiB frames, 3 channels, 2000 FOV-groups a well, 2 wells at once — 15 MiB a group, 29.3 GiB a well, 58.6 GiB
    for the run. *free* maps a folder to its free bytes, for the decision and the preflight alike.
    ``(scratch decided, run folder, exit code)``; the code is ``None`` when the preflight let the run start."""
    import logging
    import zipfile

    from pipeline import capacity

    monkeypatch.setattr(capacity, "free_bytes", lambda probe: free[Path(probe).parent])
    scratch, out = _remote_run_scratch(tmp_path, monkeypatch, host_scratch=host_scratch)
    plate = tmp_path / "in"
    plate.mkdir()
    for well in ("A01", "A02"):
        with zipfile.ZipFile(plate / f"{well}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{well}_w1_t1_f1.tif", b"\0" * MiB)
    log = logging.getLogger("process_microscopy")
    code = None
    with caplog.at_level(logging.INFO, logger=log.name):
        try:
            pio.preflight_run_scratch(out, plate, n_wells=2, n_channels=3, workers=2, image_exts=(".tif",),
                                      count_groups=lambda: 2000, log=log)
        except SystemExit as exc:
            code = exc.code
    return scratch, out, code


def test_a_node_disk_over_the_floor_but_under_the_run_s_estimate_is_left_for_the_run_folder(
        remote_env, monkeypatch, tmp_path, caplog):
    """The node's disk is chosen against a fixed 20 GB before the run's estimate exists. With 25 GB free it was
    taken, and the preflight then refused the run — 29.3 GiB for one well — that the run folder, with 1 TB, had
    always held. Now the run moves there, for itself and its workers, before any well has written a byte."""
    folders, _kinds, free = remote_env
    free[folders["scratch"]] = 25 * GB
    node = folders["scratch"] / f"allwell-{nd._user()}"
    out = tmp_path / "staging" / "runs" / "run-1" / "out"
    free.update({node: 25 * GB, out: 1024 * GB})
    scratch, out, code = _preflight_remote_run(tmp_path, monkeypatch, caplog, free=free)
    assert (scratch, code) == (node, None), "the run was refused a scratch the run folder had room for"
    assert pio.scratch_base_for(out) == out
    _as_a_spawned_worker(monkeypatch)
    assert pio.scratch_base_for(out) == out, "a worker went back to the node's disk the parent left"
    assert (f"Scratch moves off the node's own disk {node} (25.0 GiB free) to {out}: the run needs about 58.6 GiB "
            "of scratch (2 well(s) at once, 2000 FOV-group(s) each)") in caplog.messages


def test_a_node_disk_that_holds_the_run_is_kept(remote_env, monkeypatch, tmp_path, caplog):
    folders, _kinds, free = remote_env
    node = folders["scratch"] / f"allwell-{nd._user()}"
    out = tmp_path / "staging" / "runs" / "run-1" / "out"
    free.update({node: 100 * GB, out: 1024 * GB})
    scratch, out, code = _preflight_remote_run(tmp_path, monkeypatch, caplog, free=free)
    assert (scratch, code) == (node, None) and pio.scratch_base_for(out) == node
    assert not [m for m in caplog.messages if m.startswith("Scratch moves")]


def test_when_the_run_folder_is_no_roomier_the_node_disk_stays_and_the_refusal_names_the_remote_setting(
        remote_env, monkeypatch, tmp_path, caplog):
    """The advice used to name the Analyze tab's Scratch folder, which a remote run never receives
    (``_build_remote_argv`` drops ``--scratch_dir``); what moves a remote run's scratch is the host's."""
    folders, _kinds, free = remote_env
    free[folders["scratch"]] = 25 * GB
    node = folders["scratch"] / f"allwell-{nd._user()}"
    out = tmp_path / "staging" / "runs" / "run-1" / "out"
    free.update({node: 25 * GB, out: 10 * GB})
    _scratch, out, code = _preflight_remote_run(tmp_path, monkeypatch, caplog, free=free)
    assert code == 2
    assert pio.scratch_base_for(out) == node
    refusal = next(m for m in caplog.messages if m.startswith("Not enough scratch space"))
    assert ("set the host's Remote scratch dir (Remotes, under Advanced (remote paths)) to move it — the Analyze "
            "tab's Scratch folder is for runs on this computer and is not sent to a host.") in refusal
    assert "set ALLWELL_SCRATCH_DIR (or the Analyze tab's Scratch folder, under Advanced)" not in refusal


def test_a_scratch_folder_the_host_names_is_never_moved(remote_env, monkeypatch, tmp_path, caplog):
    chosen = tmp_path / "chosen-scratch"
    out = tmp_path / "staging" / "runs" / "run-1" / "out"
    _folders, _kinds, free = remote_env
    free.update({chosen: 25 * GB, out: 1024 * GB})
    _scratch, out, code = _preflight_remote_run(tmp_path, monkeypatch, caplog, free=free, host_scratch=str(chosen))
    assert code == 2
    assert pio.scratch_base_for(out) == chosen
