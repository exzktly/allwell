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
    QUEUE_WAIT_EXIT,
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
#: Queued, and stays queued until ``scancel`` (the stand-in removes the marker) or SIGTERM — then exits the way
#: srun does when its pending allocation is revoked. Gives up by itself after four seconds, so a test whose wrapper
#: never cancels fails rather than hangs.
_PENDING = (
    'echo "srun: job $JOB queued and waiting for resources" >&2\n'
    ': > "$S/pending$JOB"\n'
    "trap 'rm -f \"$S/pending$JOB\"' TERM\n"
    'i=0; while [ "$i" -lt 40 ] && [ -f "$S/pending$JOB" ]; do sleep 0.1; i=$((i+1)); done\n'
    'echo "srun: error: Job allocation $JOB has been revoked" >&2\n'
    "exit 1\n"
)
#: Allocated at once, then busy for a while before the job's script runs.
_RUN_SLOW = 'echo "srun: job $JOB has been allocated resources" >&2\nsleep 3\nshift_to_bash "$@"\n'


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fake_cluster(tmp_path: Path, outcomes: list[tuple[str, str]],
                  sinfo: str | None, *, test_only_fits: bool = True,
                  start_at: str = "2026-01-01T00:00:00") -> Path:
    """A bin dir whose ``srun`` plays *outcomes* in order: (kind, node).

    ``srun --test-only`` queues nothing and consumes no outcome; it answers
    whether the request could run, per *test_only_fits*, with *start_at* as the
    expected start, and is logged to ``state/test_only`` (with the
    ``SLURM_TIME_FORMAT`` it was run under). ``scancel`` records the job id in
    ``state/scancelled`` and ends a pending job; ``squeue`` lists a job as
    pending while it is.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    if not test_only_fits:
        (state / "test_only_fails").write_text("")
    for i, (kind, node) in enumerate(outcomes, start=1):
        body = {"boot_fail": _BOOT_FAIL, "unavailable": _CONFIG_UNAVAILABLE,
                "run": _RUN, "pending": _PENDING, "run_slow": _RUN_SLOW}[kind]
        _write_exe(state / f"outcome{i}", f"JOB={1000 + i}\nNODE={node}\n" + body)
    _write_exe(bindir / "srun", (
        f'S={shlex.quote(str(state))}\n'
        'case " $* " in *" --test-only "*)\n'
        '  printf "%s\\n" "$*" >> "$S/test_only"\n'
        '  printf "%s\\n" "${SLURM_TIME_FORMAT-unset}" >> "$S/test_only_time_format"\n'
        '  if [ -f "$S/test_only_fails" ]; then\n'
        "    echo 'srun: error: Unable to allocate resources: "
        "Requested node configuration is not available' >&2; exit 1\n"
        '  fi\n'
        f'  echo "srun: Job 9 to start at {start_at} using 16 processors on nodes n01 in partition cpu" >&2\n'
        '  exit 0;;\n'
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
    _write_exe(bindir / "scancel", (
        f'S={shlex.quote(str(state))}\n'
        'echo "$1" >> "$S/scancelled"; rm -f "$S/pending$1"\n'))
    # Called as `squeue -h -j <id> -t PD -o %i`.
    _write_exe(bindir / "squeue", (
        f'S={shlex.quote(str(state))}\n'
        'while [ $# -gt 0 ]; do [ "$1" = "-j" ] && j=$2; shift; done\n'
        'echo "$j" >> "$S/squeued"\n'
        '[ -f "$S/pending$j" ] && echo "$j"\nexit 0\n'))
    return bindir


def _run(tmp_path: Path, bindir: Path, launcher: str, **wrap_kwargs):
    pidfile = tmp_path / "run.pid"
    wrap_kwargs.setdefault("boot_retry_pause_s", 0)
    cmd = wrap_srun_launch(
        launcher, shlex.quote("echo pipeline ran on the node"),
        shlex.quote(str(pidfile)), shlex.quote(str(pidfile) + ".srun-err"),
        **wrap_kwargs)
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
    assert "node(s) n01 failed to boot — requesting another node in 0 s (retry 1; failed to boot so far: n01)" in out.stdout
    # The line the Analyze progress view reads to go back to "waiting".
    assert "[remote] scheduler: requesting allocation via: srun --partition=cpu" in out.stdout
    assert "--partition=cpu" in args[1] and "--time=1:00:00" in args[1]


def test_srun_s_own_pid_is_what_the_cancel_channel_reads(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run", "n03")], _SINFO)
    _, _, pidfile = _run(tmp_path, bindir, "srun -p cpu")
    assert pidfile.read_text().strip() == (
        bindir.parent / "state" / "pid1").read_text().strip()


def test_boot_failures_are_retried_with_no_count_limit(tmp_path):
    """Nodes that routinely take longer to boot than the cluster allows used to
    end the run after four attempts. Now only Max queue wait bounds the loop:
    six failures in a row, and the seventh node runs the job."""
    nodes = ["n01", "n03", "n05", "n07", "n08", "n09"]
    outcomes = [("boot_fail", n) for n in nodes] + [("run", "n10")]
    bindir = _fake_cluster(tmp_path, outcomes, None)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")

    assert out.returncode == 0, out.stdout + out.stderr
    assert len(args) == 7
    assert "pipeline ran on the node" in out.stdout
    assert "ERROR" not in out.stdout
    assert _excluded(args[6]) == set(nodes)
    assert ("node(s) n09 failed to boot — requesting another node in 0 s (retry 6; failed to boot so far: "
            "n01,n03,n05,n07,n08,n09)") in out.stdout


def test_when_every_fitting_node_has_failed_to_boot_they_are_tried_again(tmp_path):
    """Excluding the failed nodes can leave nothing that fits, and Slurm then
    refuses the request. That is not a reason to stop: the boot exclusions are
    dropped — the user's own kept — and the run asks again."""
    bindir = _fake_cluster(tmp_path, [("boot_fail", "n01"), ("unavailable", ""), ("run", "n01")], None)
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu --exclude=n09")

    assert out.returncode == 0, out.stdout + out.stderr
    assert len(args) == 3
    assert _excluded(args[1]) == {"n01", "n09"}
    assert _excluded(args[2]) == {"n09"}
    assert ("every node that can take this request has failed to boot at least once (n01) — "
            "trying them again in 0 s") in out.stdout
    assert "Slurm has no node that can take this request" not in out.stdout


def test_a_refusal_after_the_boot_exclusions_are_dropped_still_ends_the_run(tmp_path):
    """The reset happens once per refusal with boot exclusions in force: a
    request Slurm refuses with none in force is wrong as written and is
    reported, not retried for ever."""
    bindir = _fake_cluster(tmp_path, [("boot_fail", "n01"), ("unavailable", ""), ("unavailable", "")], None)
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu")
    assert out.returncode != 0
    assert len(args) == 3
    assert "Slurm has no node that can take this request" in out.stdout


def test_max_queue_wait_bounds_boot_retries_that_each_fail_at_once(tmp_path):
    """The per-attempt watchdog looks once a second, so attempts that fail
    instantly never meet it; the check before each attempt is what stops them."""
    bindir = _fake_cluster(tmp_path, [("boot_fail", f"n{i:02d}") for i in range(1, 30)], None)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu", queue_wait_s=2, boot_retry_pause_s=1)

    assert out.returncode == QUEUE_WAIT_EXIT, out.stdout + out.stderr
    assert 2 <= len(args) <= 5, len(args)
    assert "ERROR no node was granted within 2 s, this host's Max queue wait (failed to boot: n01" in out.stdout


def test_stop_between_two_attempts_ends_the_loop(tmp_path):
    """While the wrapper pauses before a retry no srun is running, so the
    cancel channel (`kill -TERM $(cat <pidfile>)`) has to reach the wrapper
    itself — otherwise Stop finds the previous srun gone and the loop asks
    for another node."""
    import time

    bindir = _fake_cluster(tmp_path, [("boot_fail", "n01"), ("run", "n03")], None)
    pidfile = tmp_path / "run.pid"
    cmd = wrap_srun_launch("srun -p cpu", shlex.quote("echo pipeline ran on the node"),
                           shlex.quote(str(pidfile)), shlex.quote(str(pidfile) + ".srun-err"),
                           boot_retry_pause_s=20)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)
    try:
        state = bindir.parent / "state"
        deadline = time.monotonic() + 20
        srun_pid = ""
        while time.monotonic() < deadline:
            if (state / "pid1").exists():
                srun_pid = (state / "pid1").read_text().strip()
                held = pidfile.read_text().strip() if pidfile.exists() else ""
                if held and held != srun_pid:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("the pidfile never left the first attempt's srun")
        assert pidfile.read_text().strip() == str(proc.pid)
        os.kill(int(pidfile.read_text().strip()), 15)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert not (state / "args2").exists(), "a second node was asked for after Stop"


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


def test_with_every_node_busy_and_none_asleep_nothing_is_said_to_boot(tmp_path):
    """A cluster without power saving: every node is awake, every one is full, and none is powered down. Nothing will
    boot — the job waits in the queue for one to free — so saying "Slurm will boot one" told the user to expect a
    node in minutes that was never coming."""
    sinfo = "n01 alloc\nn02 comp\nn03 alloc\nn04 drain\n"
    bindir = _fake_cluster(tmp_path, [("run", "n01")], sinfo)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert out.returncode == 0, out.stdout
    assert "will boot" not in out.stdout, out.stdout
    assert ("[remote] scheduler: every powered-on node in cpu is busy and none is powered down to boot — "
            "the job waits in the queue for one to free") in out.stdout, out.stdout


def _local_iso(seconds_from_now: int) -> str:
    """A time *seconds_from_now* in Slurm's ``standard`` format, local time as Slurm prints it."""
    from datetime import datetime, timedelta

    return (datetime.now() + timedelta(seconds=seconds_from_now)).strftime("%Y-%m-%dT%H:%M:%S")


def test_a_mix_node_does_not_count_as_room_for_the_run(tmp_path):
    """The reported case: a second app instance asked for the node shape the first had just been given. The only
    powered-on node was the one running the first run, partly allocated — ``mix`` — and with no memory or GPU left
    for a second. Counted as free, it excluded the 400-odd powered-down nodes, and the second run waited for the
    first to end. ``mix`` says only that some CPUs are taken; it is not room."""
    sinfo = "n01 mix\nn02 idle~\nn03 idle~\nn04 alloc\n"
    bindir = _fake_cluster(tmp_path, [("run", "n02")], sinfo)
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu --mem=60G --gres=gpu:1")
    assert out.returncode == 0, out.stdout
    assert _excluded(args[0]) == set()
    assert "avoiding" not in out.stdout
    assert "no free powered-on node in gpu — Slurm will boot one" in out.stdout


def test_an_idle_node_still_keeps_the_preference_beside_a_mix_one(tmp_path):
    sinfo = "n01 mix\nn02 idle~\nn03 idle\n"
    bindir = _fake_cluster(tmp_path, [("run", "n03")], sinfo, start_at=_local_iso(0))
    out, args, _ = _run(tmp_path, bindir, "srun -p gpu")
    assert _excluded(args[0]) == {"n02"}
    assert "avoiding 1 node(s) in gpu" in out.stdout


def test_a_powered_on_node_that_frees_only_later_gives_way_to_booting_one(tmp_path):
    """``srun --test-only`` says a powered-on node fits — but not for two hours, because it is running somebody's
    job with that much time left. Booting a powered-down node takes minutes, so the exclusion is dropped. The check
    runs with ``SLURM_TIME_FORMAT=standard`` so a user's own time format cannot change what is read."""
    later = _local_iso(2 * 3600)
    bindir = _fake_cluster(tmp_path, [("run", "n02")], _SINFO, start_at=later)
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert out.returncode == 0, out.stdout
    assert len(args) == 1 and _excluded(args[0]) == set()
    assert (f"[remote] scheduler: the powered-on nodes that fit this request are busy until about {later} — "
            "letting Slurm boot one instead") in out.stdout
    assert "avoiding" not in out.stdout
    formats = (tmp_path / "state" / "test_only_time_format").read_text().split()
    assert formats == ["standard"]


def test_a_powered_on_node_free_within_the_slack_keeps_the_preference(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run", "n01")], _SINFO, start_at=_local_iso(30))
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert _excluded(args[0]) == {"n02", "n04", "n06"}
    assert "busy until" not in out.stdout


def test_an_unreadable_start_time_keeps_the_preference(tmp_path):
    """``date -d`` is GNU; a login node without it cannot compare, and guessing would be worse than the backstop."""
    bindir = _fake_cluster(tmp_path, [("run", "n01")], _SINFO, start_at="Unknown")
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu")
    assert out.returncode == 0, out.stdout
    assert _excluded(args[0]) == {"n02", "n04", "n06"}


def test_a_launch_left_queued_on_powered_on_nodes_is_asked_for_again(tmp_path):
    """The backstop for what the two checks cannot see — the cluster changing between the check and the launch.
    Still queued after the wait, the job is cancelled (it is still pending, Slurm confirms) and asked for once more
    without the exclusion, with the line that puts the progress view back into its queue-wait state."""
    bindir = _fake_cluster(tmp_path, [("pending", ""), ("run", "n02")], _SINFO, start_at=_local_iso(0))
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu", pending_fallback_s=1)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "pipeline ran on the node" in out.stdout
    assert len(args) == 2
    assert _excluded(args[0]) == {"n02", "n04", "n06"}
    assert _excluded(args[1]) == set()
    state = tmp_path / "state"
    assert (state / "scancelled").read_text().split() == ["1001"]
    assert (state / "squeued").read_text().split() == ["1001"]
    assert ("[remote] scheduler: still queued for a powered-on node after 1 s — cancelled it and asking again "
            "without excluding the powered-down ones") in out.stdout
    assert "[remote] scheduler: requesting allocation via: srun -p cpu" in out.stdout
    assert not list(tmp_path.glob("run.pid.srun-err*"))


def test_a_launch_that_was_allocated_is_never_cancelled(tmp_path):
    bindir = _fake_cluster(tmp_path, [("run_slow", "n01")], _SINFO, start_at=_local_iso(0))
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu", pending_fallback_s=1)
    assert out.returncode == 0, out.stdout
    assert len(args) == 1
    assert not (tmp_path / "state" / "scancelled").exists()
    assert "still queued" not in out.stdout


def test_without_the_exclusion_a_queued_launch_is_left_alone(tmp_path):
    """Nothing excluded means nothing to give up: the queue is the queue. No watchdog, no cancel."""
    bindir = _fake_cluster(tmp_path, [("pending", "")], "n01 alloc\nn02 idle~\n")
    out, args, _ = _run(tmp_path, bindir, "srun -p cpu", pending_fallback_s=1)
    assert len(args) == 1
    assert not (tmp_path / "state" / "scancelled").exists()
    assert "still queued" not in out.stdout


def test_a_request_no_node_can_ever_take_says_what_it_asked_for_and_what_the_nodes_have(tmp_path):
    """Reported: a JupyterHub profile picked in the Remotes dialog asked for more than any node had, and the run
    ended on srun's bare "Requested node configuration is not available" — nothing said which value did not fit.
    Slurm's refusal is final (the same words would be refused again), so the wrapper prints the request beside the
    partition's node shapes, one line per shape, and the run ends."""
    from services.remote_srun_retry import NO_NODE_FITS

    bindir = _fake_cluster(tmp_path, [("unavailable", "")], None)
    _write_exe(bindir / "sinfo", (
        'case "$*" in *"%c %m %f %G"*)\n'
        '  printf "48 380000 dynamic,m8i.24xlarge (null)\\n48 380000 dynamic,m8i.24xlarge (null)\\n'
        '8 30000 static,t3.2xlarge (null)\\n";;\n'
        '*) printf "n01 idle~\\nn02 idle~\\nn03 idle~\\n";;\nesac\n'))
    launcher = "srun --partition=gen --constraint=m8i.24xlarge --cpus-per-task=96 --mem=370G"
    out, args, _ = _run(tmp_path, bindir, launcher)

    assert out.returncode != 0
    assert len(args) == 1, "a refusal Slurm will repeat was asked again"
    assert f"{NO_NODE_FITS} in gen — the scheduler command asks for: srun --partition=gen" in out.stdout
    assert "--cpus-per-task=96 --mem=370G" in out.stdout
    assert "[remote] scheduler:   2 48 380000 dynamic,m8i.24xlarge (null)" in out.stdout
    assert "[remote] scheduler:   1 8 30000 static,t3.2xlarge (null)" in out.stdout
    assert "change the scheduler command in the Remotes dialog" in out.stdout


def test_a_refusal_the_fallback_then_gets_past_says_nothing_of_the_sort(tmp_path):
    """The awake-node preference's own refusal is retried and booted: not a request no node can take."""
    from services.remote_srun_retry import NO_NODE_FITS

    bindir = _fake_cluster(tmp_path, [("unavailable", ""), ("run", "n02")], _SINFO)
    out, _args, _ = _run(tmp_path, bindir, "srun -p gpu --gres=gpu:1")
    assert out.returncode == 0, out.stdout
    assert NO_NODE_FITS not in out.stdout
