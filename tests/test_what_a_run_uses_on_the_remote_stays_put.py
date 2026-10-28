"""What a run is using on the remote is not changed or deleted under it.

Two rows of the remote pipeline review (whose tracker is in git history):

* **E5 (the GC half)** — with ``auto_gc`` on, the dataset cache evicted the
  least recently *accessed* datasets with no regard for whether a run was
  reading one; a second window's (or a colleague's) run lost its input mid-run.
* **S10 (the code-sync part)** — every run synced the pipeline's code into the
  one shared ``remote_project_dir``, so another app version, or a newer build
  started between two batches, rewrote the files a running run was importing.

Driven through ``FakeRemote``: the remote commands run under a real bash with
``HOME`` in a temporary directory, so what is asserted is what is on the host.
"""

from __future__ import annotations

import os
import shutil
import time
import zipfile

import pytest

from services.remote_cache_gc import IN_USE_WINDOW_S, Dataset, plan_eviction
from services.remote_config import RemoteHost
from services.remote_upload import DatasetCache
from tests._fake_remote import FakeRemote

DAY = 86400


@pytest.fixture
def host():
    return RemoteHost(name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.s", auto_gc=True)


@pytest.fixture
def remote(tmp_path, monkeypatch, host):
    fake = FakeRemote(tmp_path / "remote-host")
    fake.install(monkeypatch, host)
    return fake


def _dataset(remote: FakeRemote, key: str, *, size: int, accessed_days_ago: float,
             stamped_days_ago: float | None) -> None:
    """A dataset of *size* bytes, last read (atime) and last stamped as in use."""
    d = remote.path(f"~/.s/datasets/{key}")
    d.mkdir(parents=True)
    (d / "A01.zip").write_bytes(os.urandom(size))
    when = time.time() - accessed_days_ago * DAY
    for p in (d / "A01.zip", d):
        os.utime(p, (when, when))
    if stamped_days_ago is not None:
        stamp = remote.path(f"~/.s/datasets/.aw_last_used/{key}")
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        when = time.time() - stamped_days_ago * DAY
        os.utime(stamp, (when, when))


def test_gc_never_evicts_a_dataset_a_run_is_using(host, remote):
    """The run reading ``inuse`` stamped it an hour ago; nobody has read its
    files for a month (the pipeline read them once, at the start). ``stale``
    was read yesterday by someone browsing, and no run has used it for ten
    days. Over budget by one dataset: the one to go is ``stale``. Ordered by
    access time, the old GC deleted ``inuse`` — the dataset a run was reading."""
    _dataset(remote, "inuse", size=600_000, accessed_days_ago=30, stamped_days_ago=1 / 24)
    _dataset(remote, "stale", size=600_000, accessed_days_ago=1, stamped_days_ago=10)
    log: list[str] = []
    DatasetCache(host).gc(1_000_000, log_fn=log.append)
    assert (remote.exists("~/.s/datasets/inuse"), remote.exists("~/.s/datasets/stale")) \
        == (True, False), "".join(log)


def test_gc_keeps_every_in_use_dataset_even_over_budget(host, remote):
    """Two datasets both in use: the cache stays over budget, and says so."""
    _dataset(remote, "a", size=600_000, accessed_days_ago=30, stamped_days_ago=0.5)
    _dataset(remote, "b", size=600_000, accessed_days_ago=20, stamped_days_ago=2)
    log: list[str] = []
    DatasetCache(host).gc(100_000, log_fn=log.append)
    assert remote.exists("~/.s/datasets/a") and remote.exists("~/.s/datasets/b")
    assert any("kept 2 dataset(s) over budget" in line for line in log), log


def test_a_failed_listing_evicts_nothing(host, remote, monkeypatch):
    """A listing that fails says nothing about what is there."""
    _dataset(remote, "old", size=600_000, accessed_days_ago=90, stamped_days_ago=90)
    import subprocess
    monkeypatch.setattr(DatasetCache, "_ssh_script",
                        lambda self, script, **k: subprocess.CompletedProcess([], 124, "", "timed out"))
    log: list[str] = []
    DatasetCache(host).gc(1, log_fn=log.append)
    assert remote.exists("~/.s/datasets/old")
    assert any("nothing evicted" in line for line in log), log


def test_the_eviction_plan_is_oldest_unused_first():
    now = 100 * DAY
    data = [Dataset("used", 5, now - DAY), Dataset("old", 5, now - 30 * DAY),
            Dataset("older", 5, now - 60 * DAY)]
    evict, kept = plan_eviction(data, now, max_bytes=6)
    assert ([d.key for d in evict], [d.key for d in kept]) == (["older", "old"], [])
    evict, kept = plan_eviction(data, now, max_bytes=0)
    assert ([d.key for d in evict], [d.key for d in kept]) == (["older", "old"], ["used"])
    assert IN_USE_WINDOW_S >= 7 * DAY


def test_an_upload_marks_its_dataset_in_use(host, remote, tmp_path, monkeypatch):
    """The stamp the GC reads is written by the run that uses the dataset,
    through ``ensure_uploaded`` — the one way a run's input reaches the cache."""
    from services import remote_upload
    from services.remote_staging import LocalInputMode, ZippedInput

    d = tmp_path / "in"
    d.mkdir()
    with zipfile.ZipFile(d / "A01.zip", "w") as zf:
        zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"\0" * 64)
    zipped = ZippedInput(zip_dir=d, mode=LocalInputMode.ZIP, well_zips=(d / "A01.zip",))

    def fake_stream(cmd, **_k):
        # The upload itself: what rsync would have put there.
        dest = remote.path(cmd[-1].split(":", 1)[1])
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d / "A01.zip", dest / "A01.zip")
        return 0, 0.01, 0, 1

    monkeypatch.setattr(remote_upload, "_stream_rsync_with_retry", fake_stream)
    key, _sent, _elapsed = DatasetCache(host).ensure_uploaded(zipped)
    assert remote.exists(f"~/.s/datasets/.aw_last_used/{key}")


# ── S10: one run, one version of the code ───────────────────────────────────

def _tree_with(tmp_path, name: str, edit: str):
    """A copy of the repository's manifest files with one file changed."""
    from services.remote_manifest import _REQUIRED_REMOTE_FILES, repo_root

    root = tmp_path / name
    for rel in _REQUIRED_REMOTE_FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root() / rel, root / rel)
    with open(root / "process_microscopy.py", "a", encoding="utf-8") as fh:
        fh.write(f"\n# {edit}\n")
    return root


def test_two_versions_of_the_code_never_share_a_directory(host, remote, tmp_path, monkeypatch):
    """Version 1 syncs and starts a run; version 2 then syncs to the same host.
    The run must still be reading version 1's code, from the directory it was
    launched from — the shared directory gave it version 2's files instead."""
    from services import remote_runner
    from services.remote_runner import RemoteRunner

    v1, v2 = _tree_with(tmp_path, "v1", "version one"), _tree_with(tmp_path, "v2", "version two")
    monkeypatch.setattr(remote_runner, "repo_root", lambda: v1)
    first = RemoteRunner(host)
    first.sync_required_files()
    script_v1 = first._build_remote_argv(
        ["python", "process_microscopy.py", "--input_dir", "/i", "--output_dir", "/o"],
        "~/.s/datasets/k", "~/.s/runs/r/out", remote_runner.OutputFilter.FULL,
        remote_runner.LocalInputMode.ZIP)[6]

    monkeypatch.setattr(remote_runner, "repo_root", lambda: v2)
    RemoteRunner(host).sync_required_files()

    assert script_v1.startswith("~/allwell/code/") and script_v1.endswith("/process_microscopy.py")
    assert remote.path(script_v1).read_text(encoding="utf-8").endswith("# version one\n")


def test_old_code_versions_are_pruned_but_not_the_current_one(host, remote, monkeypatch, tmp_path):
    from services import remote_runner
    from services.remote_runner import RemoteRunner

    old = remote.path("~/allwell/code/0123456789abcdef")
    old.mkdir(parents=True)
    when = time.time() - 40 * DAY
    os.utime(old, (when, when))
    recent = remote.path("~/allwell/code/fedcba9876543210")
    recent.mkdir(parents=True)
    monkeypatch.setattr(remote_runner, "repo_root", lambda: _tree_with(tmp_path, "v", "now"))
    runner = RemoteRunner(host)
    runner.sync_required_files()
    current = remote.path(runner._code_dir)
    assert (old.exists(), recent.exists(), current.is_dir()) == (False, True, True)
