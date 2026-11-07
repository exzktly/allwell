"""Clearing a batched run's handshake files must never be able to remove a directory.

A host's log read ``could not clear the handshake files a previous run of this dataset left (ssh exit 1: rm: cannot
remove '/data/home/<user>': permission denied)`` -- an ``rm -rf`` naming the user's home folder itself. How that
operand arose is not established from the code (every path it builds is ``<staging>/runs/<id>/.aw_*``), so the command
is made safe whatever an operand turns out to be: ``handshake_clear_argv`` allows only files directly inside the run
directory, and uses ``rm -f``, which cannot remove a directory at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from services.remote_batch_ledger import handshake_clear_argv
from services.remote_commands import BATCH_DONE, BATCH_READY, BATCH_REANCHOR, BATCH_SKIP, BATCH_STAGED
from services.remote_config import RemoteHost
from services.remote_run_id import remote_run_paths
from services.remote_transport import _remote_join

_TEMPLATES = (BATCH_READY, BATCH_DONE, BATCH_REANCHOR, BATCH_SKIP, BATCH_STAGED)


def _operands(staging: str, batches: int = 97):
    paths = remote_run_paths(RemoteHost(name="h", ssh_alias="x", remote_staging_dir=staging), "abc123")
    flags = [f"{paths.run_dir}/{t.format(i=i)}" for i in range(1, batches + 1) for t in _TEMPLATES]
    return paths.run_dir, [paths.suspend_request, *flags]


@pytest.mark.parametrize("staging", ["~/.allwell-staging", "~", "/data/home/zakary.singer", "/scratch/me/stage/"])
def test_the_real_operands_pass_and_use_rm_f_never_r(staging):
    run_dir, operands = _operands(staging)               # 96 wells in a batch each, plus the re-anchoring batch
    argv = handshake_clear_argv(run_dir, operands)
    assert argv[:2] == ["rm", "-f"] and argv[2:] == operands
    assert not any(a.startswith("-") and "r" in a.lstrip("-") for a in argv[:2])


@pytest.mark.parametrize("stray", [
    "~", "/data/home/zakary.singer", "/data/home/zakary.singer/", "~/.allwell-staging", "~/.allwell-staging/runs",
    "~/.allwell-staging/runs/abc123", "~/.allwell-staging/runs/abc123/", "~/.allwell-staging/runs/abc123/..",
    "~/.allwell-staging/runs/abc123/sub/.aw_batch_1_ready", "~/.allwell-staging/runs/other/.aw_batch_1_ready", "",
])
def test_an_operand_that_is_not_a_file_directly_in_the_run_dir_is_refused(stray):
    run_dir, operands = _operands("~/.allwell-staging")
    with pytest.raises(ValueError, match="not files directly inside"):
        handshake_clear_argv(run_dir, [*operands[:3], stray])


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("rm") is None, reason="needs bash and rm")
def test_even_if_a_directory_got_through_the_command_leaves_it_and_its_contents(tmp_path):
    """The property itself, with the real ``rm``: the command is run with a home-like directory as an operand."""
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "thesis.txt").write_text("keep", encoding="utf-8")
    flag = home / ".aw_batch_1_ready"
    flag.write_text("", encoding="utf-8")
    command = handshake_clear_argv(str(home), [str(flag)])[:-1]      # production's own program and options
    cmd = _remote_join([*command, str(flag), str(home)])
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env={**os.environ})
    assert (home / "data" / "thesis.txt").read_text(encoding="utf-8") == "keep"
    assert not flag.exists()                              # the flag it was for is cleared
    assert result.returncode != 0 and "directory" in result.stderr.lower()    # and the run would stop, loudly
