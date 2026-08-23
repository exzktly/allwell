"""A remote run, driven end to end against a host that really has a filesystem.

Three of the four critical defects this subsystem produced were filesystem
mistakes at the boundary — a directory that was never created, a directory
removed that should not have been, an address that named the wrong place — and
none of them was visible from a unit test of the function that decided them.
The decisions were correct; the calls around them were not.

``tests/_fake_remote.py`` runs the remote commands for real against a temporary
``$HOME``, so what these assert is what the host ended up holding.

This file is the safety net for taking ``services/remote_runner.py`` apart. It
has to pass before anything moves, and it is what says a move preserved
behaviour.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from tests._fake_remote import FakeRemote


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")


@pytest.fixture
def host():
    from services.remote_runner import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


@pytest.fixture
def plate(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for name in ("A01.zip", "B02.zip"):
        with zipfile.ZipFile(in_dir / name, "w") as zf:
            zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"\0" * 256)
    (tmp_path / "out").mkdir()
    return tmp_path


@pytest.fixture
def remote(tmp_path):
    return FakeRemote(tmp_path / "remote-host")


def _argv(plate):
    return ["/usr/bin/python", "/local/process_microscopy.py",
            "--input_dir", str(plate / "in"),
            "--output_dir", str(plate / "out"),
            "--nuclear_token", "DAPI"]


def _spawn(host, plate, remote, monkeypatch, *, completed=(), **kw):
    """Drive the real ``spawn`` against the fake host; return the runner."""
    from services.remote_runner import (
        DatasetCache, LocalInputMode, OutputFilter, RemoteRunner, ZippedInput,
    )
    def _launched(_argv):
        m = MagicMock()
        m.stdout = MagicMock()
        m.stdout.__iter__ = lambda self: iter([])
        m.wait.return_value = 0
        m.returncode = 0
        m.pid = 4242
        return m

    remote.install(monkeypatch, host, on_launch=_launched)

    _KEY = "cachekey" + "0" * 24
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, z: _KEY)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, z, **k: (_KEY, 0, 0.01))
    monkeypatch.setattr(RemoteRunner, "sync_required_files",
                        lambda self, **k: None)
    monkeypatch.setattr(RemoteRunner, "_list_remote_completed_wells",
                        lambda self: set(completed))

    in_dir = plate / "in"
    zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP,
                         well_zips=(in_dir / "A01.zip", in_dir / "B02.zip"))
    runner = RemoteRunner(host)
    log: list[str] = []
    runner.spawn(zipped, plate / "out", pipeline_argv=_argv(plate),
                 output_filter=OutputFilter.FULL, log_fn=log.append, **kw)
    return runner, "".join(log)


# ── what a run leaves on the host ───────────────────────────────────────────

def test_a_run_creates_every_directory_its_control_files_need(
        host, plate, remote, monkeypatch):
    """The output directory *and* the run's own, which are not the same one.

    A re-attaching run's output lives under a previous ``run_id`` while its
    pidfile, sentinel and heartbeat stay under its own — so "the directory was
    created" has to be asked of each separately.
    """
    runner, _log = _spawn(host, plate, remote, monkeypatch)
    run = runner._run

    assert remote.exists(run.remote_output_dir), remote.tree()
    own = str(Path(run.remote_pidfile).parent)
    assert remote.exists(own), (
        f"the run's own directory {own} was never created; host holds "
        f"{remote.tree()}")


def test_the_launched_command_names_the_output_directory_it_created(
        host, plate, remote, monkeypatch):
    """The address the run computed and the address it launched against.

    These are derived in different places, which is the shape every re-attach
    defect has taken.
    """
    runner, _log = _spawn(host, plate, remote, monkeypatch)

    assert remote.launched, "no pipeline was launched"
    launched = " ".join(remote.launched[-1])
    assert runner._run.remote_output_dir in launched, launched


def test_cleanup_removes_the_run_dir_and_leaves_the_dataset_cache(
        host, plate, remote, monkeypatch):
    """Asserted on the host, not on the ``rm -rf`` argv.

    A cleanup that names the right path and never reaches the host, and one
    that reaches it and removes the wrong thing, are indistinguishable from
    the call site.
    """
    runner, _log = _spawn(host, plate, remote, monkeypatch,
                          completed=("A01", "B02"))
    run = runner._run
    own = str(Path(run.remote_pidfile).parent)
    assert remote.exists(own)

    monkeypatch.setattr(type(runner), "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(type(runner), "_reconcile_fetch",
                        lambda self, *, log: "")
    monkeypatch.setattr(type(runner), "_rsync_optional",
                        lambda self, *a, **k: None)
    runner.verify_and_cleanup(True, cleanup_mode="run", log_fn=lambda _m: None)

    assert not remote.exists(own), (
        f"the run dir survived cleanup; host holds {remote.tree()}")


def test_a_dry_run_cleanup_removes_nothing(host, plate, remote, monkeypatch):
    """The mode whose whole purpose is telling the truth about what would go."""
    runner, _log = _spawn(host, plate, remote, monkeypatch,
                          completed=("A01", "B02"))
    own = str(Path(runner._run.remote_pidfile).parent)

    monkeypatch.setattr(type(runner), "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(type(runner), "_reconcile_fetch",
                        lambda self, *, log: "")
    monkeypatch.setattr(type(runner), "_rsync_optional",
                        lambda self, *a, **k: None)
    runner.verify_and_cleanup(True, cleanup_mode="run", dry_run=True,
                              log_fn=lambda _m: None)

    assert remote.exists(own), "a dry run deleted the run dir"


def test_the_journal_records_the_address_the_run_actually_used(
        host, plate, remote, monkeypatch):
    """The record and the host must agree about where the work went."""
    J.open_run(plate / "out", spec={}, mode="remote", input_dir=plate / "in",
               argv=_argv(plate))
    runner, _log = _spawn(host, plate, remote, monkeypatch)

    journal = J.read_journal(plate / "out")
    assert journal is not None
    recorded = journal.remote.get("remote_output_dir")
    assert recorded == runner._run.remote_output_dir
    assert remote.exists(recorded), (
        f"the journal names {recorded}, which the host does not hold")


# ── the re-attaching run, where the two directories come apart ──────────────

STORED_OUT = "~/.allwell-staging/runs/run-previous/out"


def _record_a_previous_run(plate, remote):
    """A journal naming an address under a *different* run id, and a host that
    really holds output there — so the re-attach both fires and is verified."""
    from services.remote_runner import pipeline_argv_digest, source_fingerprint

    remote.path(STORED_OUT).mkdir(parents=True, exist_ok=True)
    for well in ("A01", "B02"):
        (remote.path(STORED_OUT) / f"{well}_out.zip").write_bytes(b"x")

    J.open_run(plate / "out", spec={}, mode="remote", input_dir=plate / "in",
               argv=_argv(plate))
    J.record_remote(
        plate / "out", run_id="run-previous", cache_key="k",
        host_alias="test-host",
        remote_input_dir="~/.allwell-staging/data/k",
        remote_output_dir=STORED_OUT,
        argv_digest=pipeline_argv_digest(_argv(plate), extras=[]),
        source_key=source_fingerprint(plate / "in"),
    )
    J.close_run(plate / "out", outcome="error")


def test_a_re_attaching_run_still_creates_its_own_directory(
        host, plate, remote, monkeypatch):
    """The one case where the two directories are not the same one.

    An ordinary run's ``mkdir -p …/runs/<id>/out`` makes ``runs/<id>`` on the
    way, so nothing distinguishes "created the output dir" from "created the
    run's own". A re-attaching run's output lives under a *previous* id, and
    then only an explicit ``mkdir`` puts its pidfile, sentinel and heartbeat
    anywhere — which is exactly the defect that shipped, and exactly what the
    non-re-attaching tests above cannot see.
    """
    _record_a_previous_run(plate, remote)
    runner, log = _spawn(host, plate, remote, monkeypatch,
                         completed=("A01", "B02"))

    assert "re-attaching" in log, "the run under test did not re-attach"
    run = runner._run
    assert run.remote_output_dir.endswith("run-previous/out"), run.remote_output_dir

    own = str(Path(run.remote_pidfile).parent)
    assert remote.exists(own), (
        f"the re-attached run's own directory {own} was never created; "
        f"host holds {remote.tree()}")
    assert remote.exists(run.remote_output_dir)


def test_a_re_attached_run_leaves_the_borrowed_directory_alone(
        host, plate, remote, monkeypatch):
    """Cleanup has to tell the two apart on the host, not in the argv."""
    _record_a_previous_run(plate, remote)
    runner, log = _spawn(host, plate, remote, monkeypatch,
                         completed=("A01", "B02"))
    assert "re-attaching" in log
    own = str(Path(runner._run.remote_pidfile).parent)

    monkeypatch.setattr(type(runner), "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(type(runner), "_reconcile_fetch",
                        lambda self, *, log: "")
    monkeypatch.setattr(type(runner), "_rsync_optional",
                        lambda self, *a, **k: None)
    runner.verify_and_cleanup(True, cleanup_mode="run", log_fn=lambda _m: None)

    assert remote.exists(STORED_OUT), (
        "cleanup removed the directory this run borrowed from an earlier one")
    assert not remote.exists(own), "the run's own dir survived cleanup"
