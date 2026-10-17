"""The launch command reaches the remote intact from a Windows client.

The command a run sends is one argument on the local ``ssh`` command line. On
Windows that line is built by ``list2cmdline`` and parsed again by Git for
Windows' ``ssh.exe`` before it is sent. A batched launch on the reporting
cluster had grown to about 8,700 characters — the conda guard, the ``srun``
retry wrapper and the driver loop — across seventeen lines, and failed with
``bash: -c: line 17: syntax error: unexpected end of file``: the seventeenth
line is the one that holds nearly all of it, and it arrived with its quoting
unbalanced. It was first reported the release the wrapper pushed the command
past 8,191 characters.

What the remote runs is now packed (gzip + base64) into a short command of
plain characters that the login shell unpacks and ``eval``s. These run the
packed command under real shells.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services import remote_commands as rc
from services.remote_commands import (
    UNPACK_FAILED_EXIT,
    pack_for_login_shell,
    unpack_login_shell_command,
)


_STAGING = "/home/zakary.singer/allwell_staging/runs"


def _run_dir(i: int) -> str:
    """A run directory named as ``remote_run_id`` names them: two 16-hex-digit halves, which gzip cannot shrink."""
    import hashlib

    digest = hashlib.sha256(str(i).encode()).hexdigest()
    return f"{_STAGING}/{digest[:16]}-{digest[16:32]}"


def _the_reported_batched_launch(aliases: int = 0) -> str:
    """The batched launch for the reporting cluster's L4 profile, with a
    pipeline argv the length of a real Analyze run's (47 arguments) and what
    ``_BatchedRemoteProcess._start_driver`` appends to it: the auto-threshold
    switch, the suspend request, and — for every run with an empty-well blank —
    ``--background_dir`` plus one ``--background_dir_alias`` per earlier
    attempt the journal recorded."""
    run = _run_dir(0)
    argv = (["/home/zakary.singer/miniforge3/envs/allwell/bin/python"]
            + [f"--option_{i:02d}_with_a_realistic_length" for i in range(46)]
            + [f"--background_dir={run}/bg", "--no_auto_threshold", "--suspend_file", f"{run}/.aw_suspend"]
            + [f"--background_dir_alias={_run_dir(i)}/bg" for i in range(1, aliases + 1)])
    driver = rc._batch_driver_snippet(
        run, 4, argv, heartbeat=f"{run}/.aw_heartbeat",
        suspend_request=f"{run}/.aw_suspend")
    return rc._compose_driver_cmd(
        "srun --partition=gpu-large-queue --mem=60G --gres=gpu:l4:1 "
        "--constraint=g6.4xlarge --cpus-per-task=16",
        f"{run}/run.pid", driver)


#: The most earlier attempts a launch can name: the journal keeps ``REMOTE_HISTORY_MAX`` addresses, and the
#: re-attached output directory names one more.
_MOST_ALIASES = 21


def test_the_reported_launch_packs_into_a_short_single_line():
    script = _the_reported_batched_launch()
    assert len(script) > 8191 and script.count("\n") >= 16, (
        "the fixture no longer has the size and shape that failed")
    packed = pack_for_login_shell(script)
    assert "\n" not in packed and "'" not in packed
    assert len(packed) < len(script) / 2, (len(packed), len(script))
    assert unpack_login_shell_command(packed) == script


@pytest.mark.parametrize("aliases", [0, 1, _MOST_ALIASES])
def test_the_whole_windows_command_line_stays_under_the_limit(aliases):
    """What the limit is about: the ssh command line Windows builds (``list2cmdline``) and Git for Windows'
    ssh.exe parses again, which cut a launch past 8,191 characters. Measured whole — ssh, its options, the host and
    the packed command — for a run with a blank and as many earlier attempts as the journal can name, because each
    alias is a path of incompressible hex. Measured: the packed command is 4,172 / 4,224 / 4,948 characters for
    0 / 1 / 21 aliases, and the whole line 4,418 / 4,470 / 5,194. This replaced a bar of 4,096 on the packed
    command alone, which the fixture met only because it left out the blank's flags that every blank run sends."""
    from services.run_journal import REMOTE_HISTORY_MAX

    assert _MOST_ALIASES == REMOTE_HISTORY_MAX + 1
    ssh = [r"C:\Program Files\Git\usr\bin\ssh.exe", "-tt", "-i", r"C:\Users\zakary.singer\.ssh\allwell_ed25519",
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
           "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=20", "hpc-login.example.org"]
    line = subprocess.list2cmdline(ssh + [pack_for_login_shell(_the_reported_batched_launch(aliases))])
    assert len(line) < 8191, len(line)


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_the_login_shell_runs_it_as_itself(shell, tmp_path):
    """``eval`` in the login shell keeps ``$$`` the shell's own PID and lets
    ``exec`` replace it — what the pidfile and the cancel channel rely on."""
    pidfile = tmp_path / "run.pid"
    script = (f'echo $$ > "{pidfile}"\n'
              'line_two="a \'quoted\' \\"value\\" — with an em-dash"\n'
              'exec printf "%s|%s\\n" "$line_two" "$$"')
    proc = subprocess.Popen([shell, "-c", pack_for_login_shell(script)],
                            stdout=subprocess.PIPE, text=True, encoding="utf-8")
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert pidfile.read_text().strip() == str(proc.pid)
    assert out == f'a \'quoted\' "value" — with an em-dash|{proc.pid}\n'


def test_a_remote_that_cannot_unpack_says_so_instead_of_succeeding(tmp_path):
    """No gzip: evaluating an empty script would exit 0 and read as a run that
    finished. It has to fail, and say why."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    base64_bin = next(Path(d) / "base64" for d in os.environ["PATH"].split(os.pathsep)
                      if (Path(d) / "base64").exists())
    (bindir / "base64").symlink_to(base64_bin)
    out = subprocess.run(
        ["/bin/sh", "-c", pack_for_login_shell("echo should not run")],
        capture_output=True, text=True, env={"PATH": str(bindir)}, timeout=20)
    assert out.returncode == UNPACK_FAILED_EXIT
    assert "could not unpack the launch command" in out.stdout
    assert "should not run" not in out.stdout
