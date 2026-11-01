"""The hold job's script (``services.remote_hold.compose_hold_script``), run under a real ``sh``.

The script is what ends a granted node on its own, so each rule that ends it — and each rule that must *not* — is
driven here with the script's interval and staleness limits shortened to seconds. Nothing is faked but the Slurm
environment variables and, for one test, ``nvidia-smi``.

The wrong implementations these tests are written to reject are named beside them: a script that ignores the
``busy`` marker, one that trusts a ``busy`` marker nobody is refreshing, one whose idle limit of 0 also switches the
app-gone rule off, and one that reports every SIGTERM as the time limit.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from services.remote_config import RemoteHost
from services.remote_hold import (
    HOLDS_DIRNAME,
    HoldPaths,
    compose_hold_script,
    host_key,
    hold_paths,
)
from services.remote_srun_retry import ALLOCATION_GRANTED

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")

#: Short enough for a test, long enough for a loaded machine: the script polls every second, the app counts as gone
#: after 2 s of silence (the check is "older than", so 3 s in practice), a busy marker as dead after 2 s.
FAST = dict(poll_s=1, heartbeat_stale_s=2, busy_stale_s=2)
GIVE_UP_S = 25


def _paths(d: Path) -> HoldPaths:
    return HoldPaths(dir=str(d), job=f"{d}/job", granted=f"{d}/granted", heartbeat=f"{d}/heartbeat",
                     busy=f"{d}/busy", status=f"{d}/status", release=f"{d}/release", ended=f"{d}/ended",
                     log=f"{d}/log", script=f"{d}/hold.sh")


class Hold:
    """One running hold script, with a thread beating its heartbeat the way the app does."""

    def __init__(self, tmp_path: Path, *, beating: bool = True, env: dict | None = None, busy_at_start: bool = False,
                 **script_kw):
        self.dir = tmp_path / "hold"
        self.dir.mkdir(parents=True)
        self.paths = _paths(self.dir)
        (self.dir / "heartbeat").touch()
        if busy_at_start:          # a run claimed the node while its job was still queued
            (self.dir / "busy").touch()
        kw = {**FAST, "limit_s": 3600, "idle_release_s": 0, **script_kw}
        Path(self.paths.script).write_text(compose_hold_script(self.paths, **kw), encoding="utf-8")
        self._stop_beat = threading.Event()
        self._beat = threading.Thread(target=self._beat_loop, daemon=True)
        if beating:
            self._beat.start()
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen(
            ["sh", self.paths.script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "SLURMD_NODENAME": "gpu-node-1", "SLURM_JOB_ID": "4242", "SLURM_CPUS_ON_NODE": "8",
                 **(env or {})})

    def _beat_loop(self) -> None:
        while not self._stop_beat.wait(0.3):
            (self.dir / "heartbeat").touch()

    def stop_beating(self) -> None:
        self._stop_beat.set()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def wait(self, timeout: float = GIVE_UP_S) -> str:
        """The script's output once it has exited (it must, inside *timeout*)."""
        try:
            out, _ = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the hold script was still running after {timeout} s; it should have ended the node")
        return out

    def ended(self) -> str:
        return (self.dir / "ended").read_text(encoding="utf-8").strip()

    def close(self) -> None:
        self._stop_beat.set()
        if self._beat.is_alive():
            self._beat.join(2)
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.communicate()


@pytest.fixture
def hold(tmp_path):
    made: list[Hold] = []

    def make(**kw) -> Hold:
        h = Hold(tmp_path / f"h{len(made)}", **kw) if made else Hold(tmp_path, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.close()


def _age(path: Path, seconds: float) -> None:
    """Make *path*'s mtime *seconds* old."""
    t = time.time() - seconds
    os.utime(path, (t, t))


# ── the rules that end the node ───────────────────────────────────────────────────────────────────────────────────

def test_release_ends_the_node_at_once_and_says_so(hold):
    h = hold()
    time.sleep(1.2)
    assert h.alive()
    (h.dir / "release").touch()
    out = h.wait()
    assert h.ended() == "released on request"
    assert "released: released on request" in out


def test_an_idle_node_is_released_after_the_idle_limit(hold):
    h = hold(idle_release_s=2)
    out = h.wait()
    assert h.ended() == "idle 2 s"
    assert "granted node gpu-node-1 (job 4242) released: idle 2 s" in out


def test_a_node_a_run_is_using_is_not_released_for_idleness(hold):
    """Wrong implementation: a script that never looks at the busy marker, which would end this node at ~2 s."""
    h = hold(idle_release_s=2)
    busy = h.dir / "busy"
    busy.touch()
    deadline = time.monotonic() + 3.6          # well past the 2 s idle limit, with the run's marker fresh
    while time.monotonic() < deadline:
        busy.touch()                            # the run's own heartbeat does this
        time.sleep(0.3)
        assert h.alive(), f"released while a run was using it: {h.ended() if (h.dir / 'ended').exists() else '?'}"
    busy.unlink()                               # the run ends; the idle clock starts from here
    h.wait()
    assert h.ended() == "idle 2 s"
    assert time.monotonic() - h.t0 >= 3.6, "the idle clock must start when the run ends, not when the node was granted"


def test_a_node_claimed_while_it_was_queued_starts_busy_and_its_idle_clock_never_runs(hold):
    """A run claims a granted node while its job is still queued and goes on uploading (``GrantedRun.await_node``).
    Wrong implementation: a script that counts the node idle from its start until some later look at the marker,
    which would release a node a run is about to use, mid-upload, after the idle limit."""
    h = hold(idle_release_s=2, busy_at_start=True)
    busy = h.dir / "busy"
    deadline = time.monotonic() + 3.6
    while time.monotonic() < deadline:
        busy.touch()                            # the claiming run's heartbeat
        time.sleep(0.3)
        assert h.alive(), f"released a node claimed before it started: {h.ended() if (h.dir / 'ended').exists() else '?'}"
    assert (h.dir / "status").read_text(encoding="utf-8").split() == ["state=busy", "idle_s=0"]


def test_a_busy_marker_nobody_refreshes_counts_as_not_busy(hold):
    """Wrong implementation: trusting any busy file. A run whose app died mid-run leaves one behind; the node would
    then stay granted for ever."""
    h = hold(idle_release_s=2)
    busy = h.dir / "busy"
    busy.touch()
    _age(busy, 3600)
    h.wait()
    assert h.ended() == "idle 2 s"


def test_an_app_that_stopped_beating_releases_a_node_no_run_is_using(hold):
    h = hold(beating=False)
    out = h.wait()
    assert h.ended().startswith("All-Well stopped answering")
    assert h.ended().endswith("and no run was using it")
    assert "released: All-Well stopped answering" in out


def test_an_app_that_was_already_gone_when_the_node_was_granted_releases_it_at_once(hold):
    """A hold queued, then abandoned: the app's last beat is an hour old by the time the node is granted."""
    h = hold(beating=False)
    _age(h.dir / "heartbeat", 3600)
    h.wait(timeout=8)
    assert h.ended().startswith("All-Well stopped answering")
    assert time.monotonic() - h.t0 < 8


def test_a_silent_app_does_not_release_a_node_while_a_run_is_using_it(hold):
    """The run's own watchdogs decide that run's fate; once the run ends the app-gone rule applies."""
    h = hold(beating=False)
    busy = h.dir / "busy"
    busy.touch()
    deadline = time.monotonic() + 4            # past the ~3 s the app may be silent
    while time.monotonic() < deadline:
        busy.touch()
        time.sleep(0.3)
        assert h.alive(), "released for a silent app while a run was using the node"
    busy.unlink()
    h.wait()
    assert h.ended().startswith("All-Well stopped answering")


def test_an_idle_limit_of_never_switches_off_only_the_idle_rule(hold):
    """Wrong implementation: idle 0 reading as "no release rules at all" (or as "release at once")."""
    never = hold(idle_release_s=0)
    released = hold(idle_release_s=0)
    time.sleep(2.6)                              # past where an idle limit of a couple of seconds would have fired
    assert never.alive() and released.alive(), "idle 0 (Never) released the node for idleness"
    (released.dir / "release").touch()           # Release still ends it
    never.stop_beating()                         # and so does the app going quiet
    released.wait()
    never.wait()
    assert released.ended() == "released on request"
    assert never.ended().startswith("All-Well stopped answering")


# ── Slurm ending the job ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_sigterm_at_the_time_limit_is_reported_as_the_time_limit_and_an_early_one_as_cancelled(hold):
    """Wrong implementation: one reason for every SIGTERM. Slurm's SIGTERM at --time and a scancel look the same to
    the script; only how long it has run tells them apart."""
    early = hold(limit_s=3600)
    at_limit = hold(limit_s=60)                  # within the 60 s slack of the limit from the first second
    day = hold(limit_s=24 * 3600)
    time.sleep(1.2)
    for h in (early, at_limit, day):
        h.proc.send_signal(signal.SIGTERM)
    for h in (early, at_limit, day):
        h.wait()
    assert early.ended() == "cancelled"
    assert at_limit.ended() == "reached its 1 min time limit"
    assert day.ended() == "cancelled"            # 1 s into a 24 h limit


def test_a_release_followed_at_once_by_the_cancel_is_still_reported_as_a_release(hold):
    """The app writes `release` and then `scancel`s; the SIGTERM can beat the script's next poll. The reason must
    still say it was a release, not an anonymous cancel."""
    h = hold(limit_s=3600)
    time.sleep(1.2)
    (h.dir / "release").touch()
    h.proc.send_signal(signal.SIGTERM)
    h.wait()
    assert h.ended() == "released on request"


# ── what the script tells the app ─────────────────────────────────────────────────────────────────────────────────

def test_the_granted_file_the_status_and_the_granted_line(hold, tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    smi = fake / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 'GPU 0: NVIDIA A10G (UUID: GPU-abc)'\n", encoding="utf-8")
    smi.chmod(0o755)
    h = hold(limit_s=86400, env={"PATH": f"{fake}{os.pathsep}{os.environ['PATH']}", "SLURM_JOB_PARTITION": "gpu"})
    time.sleep(1.5)
    granted = dict(line.split("=", 1) for line in (h.dir / "granted").read_text(encoding="utf-8").splitlines())
    assert granted["node"] == "gpu-node-1"
    assert granted["gpus"] == "NVIDIA A10G"
    assert granted["cpus"] == "8"
    assert granted["job"] == "4242"
    assert granted["limit_s"] == "86400"
    assert abs(int(granted["granted_at"]) - time.time()) < 30
    assert (h.dir / "status").read_text(encoding="utf-8").split()[0] == "state=idle"
    (h.dir / "release").touch()
    out = h.wait()
    assert out.splitlines()[0].startswith(ALLOCATION_GRANTED)
    assert "job 4242 on gpu-node-1 (partition gpu)" in out.splitlines()[0]


def test_the_status_file_reports_busy_then_how_long_the_node_has_been_idle(hold):
    h = hold()
    busy = h.dir / "busy"
    busy.touch()
    time.sleep(1.6)
    assert (h.dir / "status").read_text(encoding="utf-8").split() == ["state=busy", "idle_s=0"]
    busy.unlink()
    time.sleep(2.4)
    state, idle = (h.dir / "status").read_text(encoding="utf-8").split()
    assert state == "state=idle"
    assert 1 <= int(idle.removeprefix("idle_s=")) <= 4


# ── the clocks ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_idle_clock_is_this_nodes_own_and_not_the_file_servers():
    """The heartbeat check assigns `_aw_now` from the file server's stamp. Wrong implementation: computing the idle time
    with that same variable, which mixes the file server's clock with the node's and releases early (or never) when they
    disagree by minutes."""
    script = compose_hold_script(_paths(Path("/x")), limit_s=3600, idle_release_s=1800)
    idle = [line for line in script.splitlines() if "_aw_idle=$((" in line][0]
    assert "_aw_now" not in idle and "_aw_tick" in idle and "_aw_last_used" in idle
    # and `_aw_last_used` is only ever set from the node's own clock
    assert all("_aw_now" not in line for line in script.splitlines() if "_aw_last_used=" in line)


# ── the paths ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_hold_paths_are_under_the_staging_dir_and_one_per_host():
    a = RemoteHost(name="a", ssh_alias="login-a", remote_staging_dir="~/stage")
    b = RemoteHost(name="b", ssh_alias="login-b", remote_staging_dir="~/stage")
    pa, pb = hold_paths(a), hold_paths(b)
    assert pa.dir == f"~/stage/{HOLDS_DIRNAME}/{host_key(a)}"
    assert pa.dir != pb.dir
    assert hold_paths(a) == pa
    for name in ("job", "granted", "heartbeat", "busy", "status", "release", "ended", "log", "script"):
        assert getattr(pa, name).startswith(pa.dir + "/")
    # The same alias under another name is another host's node.
    assert host_key(a) != host_key(RemoteHost(name="a2", ssh_alias="login-a"))
