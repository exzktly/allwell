"""What a run is using on the remote is not changed or deleted under it.

A row of ``_Docs/Markdowns/REMOTE_REVIEW_TRACKER.md``:

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

import pytest

from services.remote_config import RemoteHost
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
