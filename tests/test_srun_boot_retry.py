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
                  sinfo: str | None, *, test_only_fits: bool = True) -> Path:
    """A bin dir whose ``srun`` plays *outcomes* in order: (kind, node).

    ``srun --test-only`` queues nothing and consumes no outcome; it answers
    whether the request could run, per *test_only_fits*, and is logged to
    ``state/test_only``.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    if not test_only_fits:
        (state / "test_only_fails").write_text("")
    for i, (kind, node) in enumerate(outcomes, start=1):
        body = {"boot_fail": _BOOT_FAIL, "unavailable": _CONFIG_UNAVAILABLE,
                "run": _RUN}[kind]
        _write_exe(state / f"outcome{i}", f"JOB={1000 + i}\nNODE={node}\n" + body)
    _write_exe(bindir / "srun", (
        f'S={shlex.quote(str(state))}\n'
        'case " $* " in *" --test-only "*)\n'
        '  printf "%s\\n" "$*" >> "$S/test_only"\n'
        '  if [ -f "$S/test_only_fails" ]; then\n'
        "    echo 'srun: error: Unable to allocate resources: "
        "Requested node configuration is not available' >&2; exit 1\n"
        '  fi\n'
        '  echo "srun: Job 9 to start at 2026-01-01T00:00:00" >&2; exit 0;;\n'
        'esac\n'
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
    assert "no free powered-on node in cpu — Slurm will boot one" in out.stdout


def test_awake_nodes_that_cannot_take_the_request_fall_back_once(tmp_path):
    """The awake nodes lack, say, the GPU the launcher asks for. One retry
    without the state-based exclusion, not counted as a boot retry."""
    bindir = _fake_cluster(tmp_path, [("unavailable", ""), ("run", "n02")], _SINFO)
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu --gres=gpu:1")
    assert out.returncode == 0, out.stdout
    assert _excluded(args[0]) == {"n02", "n04", "n06"}
    assert _excluded(args[1]) == set()
    assert "no powered-on node can take this request" in out.stdout


def test_a_request_no_awake_node_can_take_is_found_before_srun_fails(tmp_path):
    """The reported run: the awake nodes in the partition were the wrong
    instance type for ``--constraint=g6.4xlarge``, so the first real srun
    failed with "Requested node configuration is not available" — read by the
    user as an error, though the fallback then booted the right node. Slurm is
    asked first with ``--test-only``, which queues nothing, and the only real
    launch is the one that can run."""
    bindir = _fake_cluster(tmp_path, [("run", "n02")], _SINFO, test_only_fits=False)
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu --constraint=g6.4xlarge")

    assert out.returncode == 0, out.stdout
    test_only = (tmp_path / "state" / "test_only").read_text().splitlines()
    assert len(test_only) == 1
    assert _excluded(test_only[0]) == {"n02", "n04", "n06"}
    assert "--constraint=g6.4xlarge" in test_only[0]
    assert len(args) == 1 and _excluded(args[0]) == set()
    assert "Requested node configuration is not available" not in out.stdout
    assert ("[remote] scheduler: no powered-on node matches this request — Slurm "
            "will start one, which can take several minutes") in out.stdout
    assert "avoiding" not in out.stdout


def test_a_request_an_awake_node_can_take_keeps_the_preference(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run", "n03")], _SINFO)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert _excluded(args[0]) == {"n02", "n04", "n06"}
    assert "avoiding 3 node(s) in cpu" in out.stdout
    assert "no powered-on node matches" not in out.stdout


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


def test_the_job_s_first_line_arrives_before_the_job_ends(tmp_path):
    """A run sat silent after "allocated resources": the output passed through
    ``awk``, and mawk — the default awk on Debian and Ubuntu login nodes — fills
    its input buffer before handling a line, so the job's first lines waited
    for more output. Measured: a line, a pause, a second line, and the first
    one is read here well before the second is written."""
    import select
    import time

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exe(bindir / "srun", (
        'echo "srun: job 7 has been allocated resources" >&2\n'
        'echo "[remote] scheduler: allocation granted"\n'
        'sleep 4\n'
        'echo "batch 1 done"\n'))
    pidfile = tmp_path / "run.pid"
    cmd = wrap_srun_launch("srun -p cpu", shlex.quote("true"),
                           shlex.quote(str(pidfile)),
                           shlex.quote(str(pidfile) + ".srun-err"))
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    started = time.monotonic()
    proc = subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    first_at = None
    buf = b""
    try:
        while time.monotonic() - started < 15:
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if ready:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") \
                    else os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                buf += chunk
                if first_at is None and b"allocation granted" in buf:
                    first_at = time.monotonic() - started
    finally:
        proc.kill()
        proc.wait()
    assert first_at is not None, buf.decode(errors="replace")
    assert first_at < 2.5, (
        f"the job's first line arrived after {first_at:.1f}s, with the job "
        f"pausing 4s before its next line: the output is being held back")


@pytest.mark.parametrize("launcher, stray", [
    # The reported one.
    ("srun --partition=gpu-small-queue --cpus-per-task=16 --mem=60G g4dn.12xlarge",
     ["g4dn.12xlarge"]),
    ("srun --partition=gpu-small-queue --mem=60G --constraint=g4dn.12xlarge", []),
    # A bare option's value is its value, not a program.
    ("srun -p gpu -c 16 --time 4:00:00", []),
    ("srun --mem=8G a b", ["a", "b"]),
    ("srun g4dn.12xlarge -p gpu", ["g4dn.12xlarge"]),
    ("qsub -I extra", []),
    ("", []),
])
def test_a_word_srun_would_run_as_the_program_is_found(launcher, stray):
    from services.remote_srun_retry import stray_launcher_words

    assert stray_launcher_words(launcher) == stray


def test_a_launch_with_a_loose_word_is_refused_before_it_queues():
    """A saved host is not re-checked by the dialog, so the launch refuses it
    too — before the job waits out the queue to fail on execve()."""
    from services.remote_commands import _compose_driver_cmd, _compose_remote_cmd

    bad = "srun --partition=gpu-small-queue --mem=60G g4dn.12xlarge"
    with pytest.raises(ValueError, match="--constraint=g4dn.12xlarge"):
        _compose_remote_cmd(bad, "~/x/run.pid", ["python", "pm.py"], cache_hit=False)
    with pytest.raises(ValueError, match="g4dn.12xlarge"):
        _compose_driver_cmd(bad, "~/x/run.pid", "true")


def test_awake_nodes_that_are_all_busy_do_not_keep_the_job_waiting(tmp_path):
    """The reported run: 396 powered-down nodes were excluded because some
    powered-on ones existed — all of them running other jobs, so the request
    sat in the queue for a job to end while nodes that boot in minutes were
    ruled out."""
    sinfo = "n01 alloc\nn02 idle~\nn03 comp\nn04 idle~\n"
    bindir = _fake_cluster(tmp_path, [("run", "n02")], sinfo)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert out.returncode == 0, out.stdout
    assert _excluded(args[0]) == set()
    assert "no free powered-on node in cpu — Slurm will boot one" in out.stdout
    assert "avoiding" not in out.stdout
