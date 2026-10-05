"""A resumed remote run must run the groups it uploaded, however often it resumes.

An incremental run uploads a split: only the FOV-groups still missing. Two
things let such a run skip that work and report success:

* **Re-attach.** The second incremental run of an unchanged plate has the same
  argv digest and source key as the first, so the journal's recorded address
  matched and the run borrowed the first resume's host directory — which holds
  that run's outputs, including a partial well.
* **The pre-flight cache hit.** It compared the *number* of out.zips in the
  directory with the number of wells uploaded, so C01+D01 uploaded into a
  directory holding B01+C01 was "every well has output already", and the
  pipeline was replaced by ``true``. Even when it did launch, the remote runs
  without ``--incremental`` and skips any well whose output exists.

The run then reported success with C01 still partial and D01 never processed.
These drive the real ``RemoteRunner.spawn`` against ``tests/_fake_remote.py``,
whose remote commands run on a real filesystem, in the order the app runs them:
one incremental run, then another.
"""

from __future__ import annotations

import zipfile
from unittest.mock import MagicMock

import pytest

from tests._fake_remote import FakeRemote


@pytest.fixture
def host():
    from services.remote_runner import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


def _plate(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for well in ("C01", "D01"):
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            zf.writestr(f"exp_DAPI_{well}_F002_0h.tif", b"\0" * 256)
    (tmp_path / "out").mkdir()
    return in_dir


@pytest.fixture
def incremental_spawn(host, tmp_path, monkeypatch, stop_fetchers):
    """Spawn incremental runs of one plate; keys and journal are real."""
    from services import run_journal as J
    from services.remote_runner import (
        LocalInputMode,
        OutputFilter,
        RemoteRunner,
        ZippedInput,
    )
    from services.remote_upload import DatasetCache

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    remote = FakeRemote(tmp_path / "remote-host")
    launched_cmds: list[str] = []

    def _launched(argv):
        launched_cmds.append(" ".join(argv))
        m = MagicMock()
        m.stdout = MagicMock()
        m.stdout.__iter__ = lambda self: iter([])
        m.wait.return_value = 0
        m.returncode = 0
        m.pid = 4242
        return m

    remote.install(monkeypatch, host, on_launch=_launched)
    monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)
    in_dir = _plate(tmp_path)
    keys = iter(["key-first-" + "0" * 22, "key-second" + "1" * 22,
                 "key-third-" + "2" * 22])

    def _spawn(key):
        monkeypatch.setattr(DatasetCache, "cache_key", lambda self, z: key)
        monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                            lambda self, z, **k: (key, 0, 0.01))
        zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP,
                             well_zips=(in_dir / "C01.zip", in_dir / "D01.zip"),
                             incremental=True)
        runner = RemoteRunner(host)
        stop_fetchers(runner)
        J.open_run(tmp_path / "out", spec={"opts": {}}, mode="remote",
                   input_dir=str(in_dir))
        runner.spawn(zipped, tmp_path / "out", pipeline_argv=[
            "/usr/bin/python", "/local/process_microscopy.py",
            "--input_dir", str(in_dir), "--output_dir", str(tmp_path / "out"),
            "--nuclear_token", "DAPI", "--incremental"],
            output_filter=OutputFilter.FULL, log_fn=lambda _m: None)
        return runner

    return _spawn, remote, launched_cmds, keys


def _write_remote_outputs(remote, out_dir: str, wells) -> None:
    for well in wells:
        remote.path(f"{out_dir}/{well}_out.zip").parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(remote.path(f"{out_dir}/{well}_out.zip"), "w") as zf:
            zf.writestr(f"{well}_F001.tif", b"x")


def test_a_second_incremental_run_neither_borrows_nor_skips(incremental_spawn):
    """suspend -> resume -> suspend -> resume, with the key churning between."""
    spawn, remote, launched, keys = incremental_spawn
    first = spawn(next(keys))
    # The first resume finished B01 and part of C01 before being suspended.
    _write_remote_outputs(remote, first._run.remote_output_dir, ("B01", "C01"))

    second = spawn(next(keys))
    assert not second._run.reattached, (
        "the second incremental run borrowed the first one's directory, whose "
        "outputs it then counts as its own")
    assert second._run.remote_output_dir != first._run.remote_output_dir
    assert "exec true" not in launched[-1], (
        "an incremental run was declared a full cache hit and ran nothing")


def test_an_incremental_run_starts_from_an_empty_output_dir(incremental_spawn):
    """Same key twice, so the same deterministic directory: it must be emptied."""
    spawn, remote, launched, keys = incremental_spawn
    key = next(keys)
    first = spawn(key)
    _write_remote_outputs(remote, first._run.remote_output_dir, ("C01", "D01"))

    second = spawn(key)
    assert second._run.remote_output_dir == first._run.remote_output_dir
    assert not remote.exists(f"{second._run.remote_output_dir}/C01_out.zip"), (
        "the previous run's partial C01 output was still in the directory, so "
        "the pipeline would skip C01 as already done")
    assert "exec true" not in launched[-1]


def test_the_cache_hit_compares_wells_not_counts():
    from services.remote_run_id import _pipeline_launch_is_pointless as pointless

    argv = ["python", "pm.py"]
    assert pointless(argv, 2, {"B01", "C01"}, uploaded={"C01", "D01"}) is False
    assert pointless(argv, 2, {"C01", "D01"}, uploaded={"C01", "D01"}) is True
    assert pointless(argv, 2, {"C01", "D01"}, uploaded={"C01", "D01"},
                     incremental=True) is False
