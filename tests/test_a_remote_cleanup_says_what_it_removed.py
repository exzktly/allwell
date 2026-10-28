"""A remote cleanup says what it actually removed, and a batch that cannot be pruned does not run.

Both sites ran an ``rm -rf`` over ssh with ``check=False`` and threw the result
away (review P2 in the remote pipeline review (whose tracker is in git history)): the run's
cleanup then logged "removed …" over a directory still on the host, and a batch
whose staged directory could be neither pruned nor cleared went ahead holding a
stray well that would be run and reported twice.
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from services.remote_batch_staging import prune_staged
from services.remote_runner import RemoteRunner


def _answers(*codes):
    """An ``_ssh_run`` stand-in that answers each call with the next exit code."""
    calls: list[list[str]] = []
    it = iter(codes)

    def ssh_run(argv, *, check, **kw):
        calls.append(list(argv))
        rc = next(it)
        return subprocess.CompletedProcess(argv, rc, "", "" if rc == 0 else "ssh: connect to host hpc: Connection reset")
    return ssh_run, calls


def test_a_cleanup_rm_that_failed_is_not_reported_as_removed():
    runner = object.__new__(RemoteRunner)
    ssh_run, calls = _answers(255)
    runner._cache = types.SimpleNamespace(_ssh_run=ssh_run)
    log: list[str] = []
    assert runner._remove_remote("/r/runs/r1", "run dir r1", log.append) is False
    assert calls == [["rm", "-rf", "/r/runs/r1"]]
    text = "".join(log)
    assert "removed run dir" not in text
    assert "could not remove run dir r1 (ssh: connect to host hpc: Connection reset)" in text


def test_a_cleanup_rm_that_ran_says_removed():
    runner = object.__new__(RemoteRunner)
    ssh_run, _calls = _answers(0)
    runner._cache = types.SimpleNamespace(_ssh_run=ssh_run)
    log: list[str] = []
    assert runner._remove_remote("/r/runs/r1", "run dir r1", log.append) is True
    assert "".join(log) == "[remote] removed run dir r1.\n"


def test_a_batch_that_can_be_neither_pruned_nor_cleared_does_not_go_ahead():
    ssh_run, calls = _answers(255, 255)
    cache = types.SimpleNamespace(_ssh_run=ssh_run)
    with pytest.raises(RuntimeError, match="could not prune or clear the staged directory /r/b4"):
        prune_staged(cache, "/r/b4", [Path("A01.zip")], "batch 4", lambda m: None)
    assert calls[-1] == ["rm", "-rf", "/r/b4"]
