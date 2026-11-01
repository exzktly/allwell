"""The storage hooks, driven through a real remote run rather than called directly.

``services.remote_storage`` is reached from two places inside a run, and both are
best-effort on purpose — a run must never fail because the storage bookkeeping
could not be done:

* ``remote_process._run_prepared`` → ``label_and_sweep`` at every run start: it
  labels the run's folders and removes expired ones on a background thread.
* ``RemoteRunner.verify_and_cleanup`` → ``mark_fetched`` once a run's output has
  verified, and ``is_kept`` before Auto-delete removes anything.

Best-effort means a hook that breaks says one log line and the run goes on, so a
broken hook is invisible to every test that only watches the run succeed. That is
exactly how the Storage browser's bash-before-5.2 parse failure would have hidden:
every label and every sweep failing silently on the reporting host. These tests
drive the real ``RemoteRunner.spawn`` and ``verify_and_cleanup`` against
``tests/_fake_remote.py`` — every remote command runs in real bash under a
temporary ``$HOME`` — and assert what the host ended up holding.
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import remote_storage as rs
from services.remote_fetch import FetchedOutputVerifier
from tests._fake_remote import FakeRemote

DAY = 86400
#: A cache key of the shape the app gives one (a 32-hex content fingerprint).
KEY = "c0ffee00" * 4
#: An old run of another plate, labelled, downloaded, untouched for 40 days.
OLD_RUN = "run-00000000000000a1-00000000000000b2"
#: Another one whose results were never downloaded.
UNFETCHED_RUN = "run-00000000000000c3-00000000000000d4"


@pytest.fixture
def plate(tmp_path):
    root = tmp_path / "MyProject"
    in_dir = root / "in"
    in_dir.mkdir(parents=True)
    for name in ("A01.zip", "B02.zip"):
        with zipfile.ZipFile(in_dir / name, "w") as zf:
            zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"\0" * 256)
    (root / "out").mkdir()
    return root


@pytest.fixture
def remote(tmp_path):
    return FakeRemote(tmp_path / "remote-host")


def _host(expiry_days: int = 0):
    from services.remote_config import RemoteHost
    return RemoteHost(name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
                      cache_budget_gb=10, measured_bandwidth_mbps=100.0, storage_expiry_days=expiry_days)


def _age(path: Path, days: float) -> None:
    t = time.time() - days * DAY
    for p in [*path.rglob("*"), path]:
        os.utime(p, (t, t), follow_symlinks=False)


def _seed_old_run(remote: FakeRemote, name: str, *, fetched: bool) -> Path:
    d = remote.path(f"~/.allwell-staging/runs/{name}")
    (d / "out").mkdir(parents=True)
    (d / "out" / "A01_out.zip").write_bytes(b"old")
    (d / rs.RUN_LABEL).write_text(json.dumps({"run_id": name, "output_dir": "/elsewhere/out"}))
    if fetched:
        (d / rs.RUN_FETCHED).write_text("1")
    _age(d, 40)
    return d


@pytest.fixture
def spawn(stop_fetchers, monkeypatch, remote, plate):
    """The real ``RemoteRunner.spawn`` against the fake host, as
    ``tests/test_a_remote_run_end_to_end.py`` drives it, with a realistic key."""
    def _spawn(host, *, completed=()):
        from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner, ZippedInput
        from services.remote_upload import DatasetCache

        def _launched(_argv):
            m = MagicMock()
            m.stdout = MagicMock()
            m.stdout.__iter__ = lambda self: iter([])
            m.wait.return_value = 0
            m.returncode = 0
            m.pid = 4242
            return m

        remote.install(monkeypatch, host, on_launch=_launched)
        # The upload is not under test; its dataset folder is there, as an upload leaves it.
        remote.path(f"~/.allwell-staging/datasets/{KEY}").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(DatasetCache, "cache_key", lambda self, z: KEY)
        monkeypatch.setattr(DatasetCache, "ensure_uploaded", lambda self, z, **k: (KEY, 0, 0.01))
        monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)
        monkeypatch.setattr(FetchedOutputVerifier, "_list_remote_completed_wells", lambda self: set(completed))

        in_dir = plate / "in"
        zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP,
                             well_zips=(in_dir / "A01.zip", in_dir / "B02.zip"))
        runner = RemoteRunner(host)
        stop_fetchers(runner)
        log: list[str] = []
        argv = ["/usr/bin/python", "/local/process_microscopy.py", "--input_dir", str(in_dir),
                "--output_dir", str(plate / "out"), "--nuclear_token", "DAPI"]
        runner.spawn(zipped, plate / "out", pipeline_argv=argv, output_filter=OutputFilter.FULL,
                     log_fn=log.append)
        return runner, log

    return _spawn


def _verify_passes(monkeypatch):
    monkeypatch.setattr(FetchedOutputVerifier, "_verify_local_manifest", lambda self, *, log: [])
    monkeypatch.setattr(FetchedOutputVerifier, "_reconcile_fetch", lambda self, *, log: "")
    monkeypatch.setattr(FetchedOutputVerifier, "_rsync_optional", lambda self, *a, **k: True)


def _wait_until(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_run_start_labels_the_run_and_its_dataset_on_the_host(spawn, remote, plate):
    runner, log = spawn(_host())
    run = runner._run
    label = remote.path(f"~/.allwell-staging/runs/{run.run_id}/{rs.RUN_LABEL}")
    assert label.is_file(), f"no label on the host; log:\n{''.join(log)}\nhost:\n{remote.tree()}"
    saved = json.loads(label.read_text())
    assert (saved["project"], saved["output_dir"], saved["run_id"], saved["cache_key"]) == (
        "MyProject", str(plate / "out"), run.run_id, KEY)
    ds_label = remote.path(f"~/.allwell-staging/datasets/{rs.DATASET_LABEL_DIR}/{KEY}.json")
    assert json.loads(ds_label.read_text())["project"] == "MyProject"
    # The dataset folder is the pipeline's input: the label lives beside it, never in it.
    assert sorted(p.name for p in remote.path(f"~/.allwell-staging/datasets/{KEY}").iterdir()) == []
    assert not any("storage: could not" in line for line in log), "".join(log)


def test_a_run_start_removes_an_expired_downloaded_folder_and_keeps_the_rest(spawn, remote):
    old = _seed_old_run(remote, OLD_RUN, fetched=True)
    unfetched = _seed_old_run(remote, UNFETCHED_RUN, fetched=False)
    runner, log = spawn(_host(expiry_days=30))
    # The sweep runs on a background thread so the run never waits for it.
    assert _wait_until(lambda: not old.exists()), f"the expired folder survived; log:\n{''.join(log)}"
    assert unfetched.is_dir(), "a folder whose results were never downloaded was removed"
    assert remote.exists(runner._run.remote_output_dir), "the starting run's own folder went"
    assert _wait_until(lambda: any(f"removed expired run {OLD_RUN}" in line for line in log)), "".join(log)


def test_without_a_host_rule_a_run_start_removes_nothing(spawn, remote):
    old = _seed_old_run(remote, OLD_RUN, fetched=True)
    spawn(_host(expiry_days=0))
    time.sleep(1.0)                                # nothing to wait for: the sweep is not started
    assert old.is_dir()


def test_a_verified_full_fetch_marks_the_folder_downloaded_on_the_host(spawn, remote, monkeypatch):
    runner, _log = spawn(_host(), completed=("A01", "B02"))
    marker = remote.path(f"~/.allwell-staging/runs/{runner._run.run_id}/{rs.RUN_FETCHED}")
    assert not marker.exists(), "a run start must leave its folder not-downloaded"
    _verify_passes(monkeypatch)
    assert runner.verify_and_cleanup(True, cleanup_mode="none", log_fn=lambda _m: None)
    assert marker.is_file(), remote.tree()


def test_auto_delete_leaves_a_folder_marked_keep_forever_on_the_host(spawn, remote, monkeypatch):
    runner, _log = spawn(_host(), completed=("A01", "B02"))
    own = remote.path(f"~/.allwell-staging/runs/{runner._run.run_id}")
    (own / rs.RUN_EXPIRY).write_text("keep\n")
    _verify_passes(monkeypatch)
    log: list[str] = []
    assert runner.verify_and_cleanup(True, cleanup_mode="run", log_fn=log.append)
    assert own.is_dir(), "Auto-delete removed a folder marked Keep forever"
    assert any("Keep forever" in line for line in log), "".join(log)


def test_auto_delete_still_removes_a_folder_that_is_not_kept(spawn, remote, monkeypatch):
    """The other half: honouring Keep must not stop Auto-delete for everyone."""
    runner, _log = spawn(_host(), completed=("A01", "B02"))
    own = remote.path(f"~/.allwell-staging/runs/{runner._run.run_id}")
    _verify_passes(monkeypatch)
    assert runner.verify_and_cleanup(True, cleanup_mode="run", log_fn=lambda _m: None)
    assert not own.exists(), remote.tree()
