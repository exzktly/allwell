"""The ``srun`` wrapper prefers awake nodes and retries a node that fails to boot.

These run the command :func:`services.remote_srun_retry.wrap_srun_launch`
produces, under the system ``sh`` (dash on Debian, as a login shell may be),
with stand-in ``srun`` and ``sinfo`` programs on ``PATH``. The stand-in ``srun``
follows a script of outcomes, one per invocation, and records the arguments it
was given, so the tests read what Slurm would have been asked for.

Nothing here talks to a scheduler: what is pinned is the shell's own logic —
which nodes it excludes, when it retries, when it stops, and what it says.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

from services.remote_srun_retry import (
    SRUN_BOOT_RETRIES,
    is_srun,
    wrap_srun_launch,
)

_BOOT_FAIL = (
    'echo "srun: job $JOB queued and waiting for resources" >&2\n'
    'echo "srun: job $JOB has been allocated resources" >&2\n'
    'echo "srun: error: Nodes $NODE are still not ready" >&2\n'
    'echo "srun: error: Something is wrong with the boot of the nodes." >&2\n'
    "exit 1\n"
)
_CONFIG_UNAVAILABLE = (
    "echo 'srun: error: Unable to allocate resources: "
    "Requested node configuration is not available' >&2\nexit 1\n"
)
_RUN = 'echo "srun: job $JOB has been allocated resources" >&2\n' \
       'shift_to_bash "$@"\n'


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fake_cluster(tmp_path: Path, outcomes: list[tuple[str, str]],
                  sinfo: str | None) -> Path:
    """A bin dir whose ``srun`` plays *outcomes* in order: (kind, node)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    for i, (kind, node) in enumerate(outcomes, start=1):
        body = {"boot_fail": _BOOT_FAIL, "unavailable": _CONFIG_UNAVAILABLE,
                "run": _RUN}[kind]
        _write_exe(state / f"outcome{i}", f"JOB={1000 + i}\nNODE={node}\n" + body)
    _write_exe(bindir / "srun", (
        f'S={shlex.quote(str(state))}\n'
        'n=$(( $(cat "$S/count" 2>/dev/null || echo 0) + 1 ))\n'
        'echo "$n" > "$S/count"\n'
        'printf "%s\\n" "$*" > "$S/args$n"\n'
        'echo $$ > "$S/pid$n"\n'
        # Run everything after "bash -lc" the way srun would on the node.
        'shift_to_bash() { while [ "$1" != "bash" ]; do shift; done; "$@"; }\n'
        '. "$S/outcome$n"\n'
    ))
    if sinfo is not None:
        _write_exe(bindir / "sinfo", f"cat <<'EOF'\n{sinfo}EOF\n")
    return bindir


def _run(tmp_path: Path, bindir: Path, launcher: str):
    pidfile = tmp_path / "run.pid"
    cmd = wrap_srun_launch(
        launcher, shlex.quote("echo pipeline ran on the node"),
        shlex.quote(str(pidfile)), shlex.quote(str(pidfile) + ".srun-err"))
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True,
                         env=env, timeout=60)
    state = bindir.parent / "state"
    calls = int((state / "count").read_text()) if (state / "count").exists() else 0
    args = [(state / f"args{i}").read_text().strip() for i in range(1, calls + 1)]
    return out, args, pidfile


def _excluded(arg_line: str) -> set[str]:
    for tok in arg_line.split():
        if tok.startswith("--exclude="):
            return set(tok.split("=", 1)[1].split(","))
    return set()


_SINFO = "n01 idle\nn02 idle~\nn03 mix\nn04 down~\nn05 alloc\nn06 drain\n"


def test_a_node_that_fails_to_boot_is_excluded_and_the_run_retried(tmp_path):
    bindir = _fake_cluster(tmp_path, [("boot_fail", "n01"), ("run", "n03")], _SINFO)
    out, args, pidfile = _run(tmp_path, bindir, "srun --partition=cpu --time=1:00:00")

    assert out.returncode == 0, out.stdout + out.stderr
    assert "pipeline ran on the node" in out.stdout
    assert len(args) == 2
    # First attempt: every node not powered on is avoided, nothing else.
    assert _excluded(args[0]) == {"n02", "n04", "n06"}
    # Second: the node that failed to boot is added.
    assert _excluded(args[1]) == {"n01", "n02", "n04", "n06"}
    assert "node(s) n01 failed to boot — requesting another node (retry 1 of 3)" in out.stdout
    # The line the Analyze progress view reads to go back to "waiting".
    assert "[remote] scheduler: requesting allocation via: srun --partition=cpu" in out.stdout
    assert "--partition=cpu" in args[1] and "--time=1:00:00" in args[1]


def test_srun_s_own_pid_is_what_the_cancel_channel_reads(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run", "n03")], _SINFO)
    _, _, pidfile = _run(tmp_path, bindir, "srun -p cpu")
    assert pidfile.read_text().strip() == (
        bindir.parent / "state" / "pid1").read_text().strip()


def test_retries_stop_after_three_and_name_every_node(tmp_path):
    outcomes = [("boot_fail", f"n0{i}") for i in (1, 3, 5, 7)]
    bindir = _fake_cluster(tmp_path, outcomes, _SINFO)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")

    assert len(args) == SRUN_BOOT_RETRIES + 1 == 4
    assert out.returncode != 0
    assert "ERROR nodes failed to boot on all 4 attempts" in out.stdout
    for node in ("n01", "n03", "n05", "n07"):
        assert node in out.stdout.splitlines()[-1]


def test_the_user_s_own_exclusions_survive(tmp_path):
    """Slurm takes the last --exclude it is given, so a second flag would
    silently drop the user's; they are merged into the one passed."""
    bindir = _fake_cluster(tmp_path, [("run", "n03")], _SINFO)
    _, args, _ = _run(tmp_path, bindir, "srun -p cpu --exclude=n09 -x n10")
    assert args[0].count("--exclude") == 1
    assert _excluded(args[0]) == {"n02", "n04", "n06", "n09", "n10"}


def test_with_no_awake_node_slurm_is_left_to_boot_one(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run", "n02")], "n02 idle~\nn04 down~\n")
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert _excluded(args[0]) == set()
    assert "no powered-on node in cpu — Slurm will boot one" in out.stdout


def test_awake_nodes_that_cannot_take_the_request_fall_back_once(tmp_path):
    """The awake nodes lack, say, the GPU the launcher asks for. One retry
    without the state-based exclusion, not counted as a boot retry."""
    bindir = _fake_cluster(tmp_path, [("unavailable", ""), ("run", "n02")], _SINFO)
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu --gres=gpu:1")
    assert out.returncode == 0, out.stdout
    assert _excluded(args[0]) == {"n02", "n04", "n06"}
    assert _excluded(args[1]) == set()
    assert "no powered-on node can take this request" in out.stdout


def test_a_failure_that_is_not_a_boot_failure_is_not_retried(tmp_path):
    bindir = _fake_cluster(tmp_path, [("unavailable", ""), ("run", "n01")], None)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert len(args) == 1
    assert out.returncode != 0


def test_without_sinfo_nothing_is_excluded(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run", "n01")], None)
    _, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert _excluded(args[0]) == set()


@pytest.mark.parametrize("launcher, expected", [
    ("srun --partition=gpu", True),
    ("/opt/slurm/bin/srun -p cpu", True),
    ("qsub -I", False),
    ("", False),
    ("salloc -p cpu", False),
])
def test_only_srun_launchers_are_wrapped(launcher, expected):
    assert is_srun(launcher) is expected


def test_the_sh_on_this_machine_is_a_posix_one():
    """The wrapper must run under whatever /bin/sh a login node has; the CI
    machine's is dash on Debian/Ubuntu, which rejects bash-only syntax."""
    assert os.path.exists("/bin/sh")


def test_the_job_script_reaches_srun_byte_for_byte(tmp_path):
    """The real composer's inner script — conda activation, quoted paths,
    ``$SLURM_JOB_ID`` — must arrive at srun exactly as the unwrapped launch
    delivers it to any other launcher. A quoting slip in the wrapper would hand
    the node a different script than the one composed."""
    from services.remote_commands import _compose_remote_cmd

    argv = ["python", "process_microscopy.py", "--input_dir", "/scratch/a b/in"]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("srun", "mysched"):
        # Record the argument after "bash -lc", then stop.
        _write_exe(bindir / name, (
            'while [ "$1" != "-lc" ]; do shift; done\n'
            f'printf "%s" "$2" > {shlex.quote(str(tmp_path))}/{name}.inner\n'))
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    for launcher in ("srun -p cpu", "mysched -p cpu"):
        cmd = _compose_remote_cmd(launcher, str(tmp_path / "run.pid"), argv,
                                  cache_hit=False)
        subprocess.run(["sh", "-c", cmd], env=env, capture_output=True, timeout=60)
    wrapped = (tmp_path / "srun.inner").read_text()
    unwrapped = (tmp_path / "mysched.inner").read_text()
    assert wrapped == unwrapped
    assert "process_microscopy.py --input_dir '/scratch/a b/in'" in wrapped
