"""A control command that times out returns at its timeout, even when something it started still holds its pipes.

On Windows, ``subprocess.run(..., capture_output=True, timeout=…)`` kills the child at the timeout and then calls
``communicate()`` with **no** timeout, to collect the output its reader threads had gathered. Those threads read until
the pipes close, and a process the child started — ssh's ProxyJump hop, a ProxyCommand — inherits the pipes and keeps
them open after the child is killed. The call then never returns. That is how a heartbeat thread went silent on a
Windows machine: one beat timed out during a network blip, and the thread sat in that ``communicate()`` for good — no
warning, no further beat — until the granted node's script, hearing nothing for ten minutes, released the node and
Slurm cancelled the run's step.

The Windows branch is reached here on Linux by setting ``subprocess._mswindows`` — the flag ``run`` reads to pick it —
at the moment the child is killed, after ``Popen`` has been built the POSIX way. ``run``'s own code then takes its
Windows branch, and the POSIX ``communicate()`` it calls blocks on a pipe a grandchild holds just as the Windows reader
threads do.

Wrong implementation this rejects: ``subprocess.run`` with a timeout (the call takes as long as the grandchild lives).
"""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from services.remote_transport import SSH_TIMED_OUT, ssh_control

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")

GRANDCHILD_S = 20


class _Host:
    """An ``ssh`` that is really ``sh``: it leaves a grandchild holding its stdout and stderr, then hangs."""

    ssh_alias = "test-host"

    def ssh_argv(self, allocate_tty: bool = False, **_kw) -> list[str]:
        return ["sh", "-c", f"sleep {GRANDCHILD_S} & sleep {GRANDCHILD_S}", "ssh"]


def _windows_after_kill(monkeypatch) -> None:
    """From the child's kill on, ``subprocess`` behaves as it does on Windows (see the module docstring)."""
    kill = subprocess.Popen.kill

    def kill_then_windows(self):
        kill(self)
        monkeypatch.setattr(subprocess, "_mswindows", True)

    monkeypatch.setattr(subprocess.Popen, "kill", kill_then_windows)


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
def test_a_timed_out_command_returns_at_its_timeout_though_a_grandchild_holds_its_pipes(monkeypatch, windows):
    if windows:
        _windows_after_kill(monkeypatch)
    began = time.monotonic()
    result = ssh_control(_Host(), ["touch", "x"], check=False, timeout_s=1.0, what="touch x")
    took = time.monotonic() - began
    assert took < 8, f"the command took {took:.1f} s against a 1 s timeout: the grandchild held it"
    assert result.returncode == SSH_TIMED_OUT
    assert "did not answer within 1s" in result.stderr


def test_a_timed_out_command_under_check_raises_at_its_timeout(monkeypatch):
    _windows_after_kill(monkeypatch)
    began = time.monotonic()
    with pytest.raises(RuntimeError, match="did not answer within 1s"):
        ssh_control(_Host(), ["touch", "x"], check=True, timeout_s=1.0, what="touch x")
    assert time.monotonic() - began < 8


class _Echo:
    ssh_alias = "test-host"

    def __init__(self, script: str) -> None:
        self.script = script

    def ssh_argv(self, allocate_tty: bool = False, **_kw) -> list[str]:
        return ["sh", "-c", self.script, "ssh"]


def test_a_command_that_answers_is_unchanged():
    """The control: output, exit code, stdin as bytes, and check=True's error all still come through."""
    ok = ssh_control(_Echo('echo out; echo err >&2; exit 3'), [], check=False, timeout_s=10)
    assert (ok.returncode, ok.stdout, ok.stderr) == (3, "out\n", "err\n")
    piped = ssh_control(_Echo("cat"), [], check=False, timeout_s=10, stdin=b"a\nb\n")
    assert (piped.returncode, piped.stdout) == (0, "a\nb\n")
    with pytest.raises(subprocess.CalledProcessError, match="said no"):
        ssh_control(_Echo("echo said no >&2; exit 2"), [], check=True, timeout_s=10, what="x")
