"""Clearing a batched run's handshake files must be short, and must be unable to remove anything but those files.

A host's log read ``could not clear the handshake files a previous run of this dataset left (ssh exit 1: rm: cannot
remove '/data/home/<user>': permission denied)`` -- an ``rm -rf`` naming the user's home folder itself, right after
``zip mode: 96 wells -- batched``. The command named every flag of every batch: 486 operands and 32 000 characters for
that run, which does not reliably reach the host whole. A command cut where an operand began (``"$HOME"``) is ``rm -rf``
of the home folder. (That this is what happened on that host is inferred from the length and the operand, not shown.)

It is now a ``find`` of the run directory's top level for files, whose length does not depend on the batch count, and
the tests below run it with the real ``find`` -- whole, and cut at every position -- against a home folder with data in
it.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from services.remote_batch_ledger import handshake_clear_argv
from services.remote_transport import _remote_join

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("find") is None,
                                reason="needs bash and find")

RUN_DIR = "~/.allwell-staging/runs/0123456789abcdef"


def _world(tmp_path):
    """A home folder with data, a staging directory, and a run directory holding flags and things that are not."""
    home = tmp_path / "home"
    run = home / ".allwell-staging" / "runs" / "0123456789abcdef"
    (home / "data").mkdir(parents=True)
    (home / "data" / "thesis.txt").write_text("keep", encoding="utf-8")
    (home / "notes.txt").write_text("keep", encoding="utf-8")
    for d in ("keep", "in", "bg", ".aw_batch_9_ready"):               # the last is a directory named like a flag
        (run / d).mkdir(parents=True)
        (run / d / "inner.txt").write_text("keep", encoding="utf-8")
    flags = [".aw_suspend_request"] + [f".aw_batch_{i}_{k}" for i in (1, 2, 97) for k in ("ready", "done", "reanchor")]
    for name in flags:
        (run / name).write_text("", encoding="utf-8")
    (run / "run.pid").write_text("1", encoding="utf-8")
    return home, run, flags


def _run(command: str, home) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "HOME": str(home)})


def _snapshot(root) -> dict[str, str]:
    return {str(p.relative_to(root)): (p.read_text(encoding="utf-8") if p.is_file() else "<dir>")
            for p in sorted(root.rglob("*"))}


def test_the_command_is_short_and_does_not_grow_with_the_batches():
    command = _remote_join(handshake_clear_argv(RUN_DIR))
    assert len(command) < 300, command
    assert "_ready" not in command and "_done" not in command      # no flag of any batch is named


def test_it_clears_every_flag_of_any_batch_and_nothing_else(tmp_path):
    home, run, flags = _world(tmp_path)
    before = _snapshot(home)
    result = _run(_remote_join(handshake_clear_argv(RUN_DIR)), home)
    assert result.returncode == 0, result.stderr
    after = _snapshot(home)
    assert [f for f in flags if (run / f).exists()] == []               # including batch 97's, beyond this run's count
    removed = sorted(set(before) - set(after))
    assert removed == sorted(str(run.relative_to(home) / f) for f in flags)
    assert set(after) == set(before) - set(removed)                      # and nothing was added or changed
    assert (run / ".aw_batch_9_ready" / "inner.txt").exists()           # a directory named like a flag stays
    assert (run / "keep" / "inner.txt").exists() and (run / "run.pid").exists()
    assert (home / "data" / "thesis.txt").read_text(encoding="utf-8") == "keep"


def test_cut_off_at_any_position_it_removes_nothing_outside_the_flag_files(tmp_path):
    """Whatever happens to a long command on its way to the host, no prefix of this one can delete anything but a
    handshake file: it has no operand to be cut into a home folder."""
    command = _remote_join(handshake_clear_argv(RUN_DIR))
    flag_names = {".aw_suspend_request"} | {f".aw_batch_{i}_{k}" for i in (1, 2, 97) for k in ("ready", "done", "reanchor")}
    for cut in range(1, len(command) + 1):
        home, run, flags = _world(tmp_path / f"cut{cut}")
        before = _snapshot(home)
        _run(command[:cut], home)
        gone = {pth for pth in before if pth not in _snapshot(home)}
        assert all(os.path.basename(g) in flag_names and os.path.dirname(g) == str(run.relative_to(home)) for g in gone), (
            cut, command[:cut], sorted(gone))
        assert (home / "data" / "thesis.txt").exists() and (home / "notes.txt").exists()


def test_the_old_command_cut_where_an_operand_began_would_have_emptied_the_home_folder(tmp_path):
    """The mechanism this replaces, shown on the stand-in home: the old form of the command cut at an operand's start."""
    home, run, _ = _world(tmp_path)
    old = _remote_join(["rm", "-rf", f"{RUN_DIR}/.aw_suspend_request", f"{RUN_DIR}/.aw_batch_1_ready"])
    cut = old[:old.rindex('"$HOME"') + len('"$HOME"')]                  # ...the second operand, cut after its "$HOME"
    _run(cut, home)
    assert not (home / "data" / "thesis.txt").exists()                  # what the user's home would have lost
