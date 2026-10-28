"""One Slurm allocation for every batch, instead of one per batch.

Batched remote mode uploads and runs a plate in slices so peak remote disk stays
small. On a scheduler-backed host it used to pay for that with a queue wait *per
batch*: every batch composed its own ``srun`` and launched it over its own ssh,
so an eight-batch plate queued eight times and handed the node back in between.

Now one driver holds the allocation and the batches come to it. These cover the
three things that has to get right — the driver's own loop, the handshake with
the local side, and the walltime arithmetic that a held allocation forces.
"""

from __future__ import annotations

import queue
import threading
import types
from pathlib import Path

import pytest

from services.remote_config import (
    MAX_HELD_ALLOCATION_S,
    launcher_time_seconds,
    parse_slurm_time,
    render_slurm_time,
    scale_launcher_time,
)
from services.remote_commands import (
    BATCH_DONE,
    BATCH_READY,
    _batch_driver_snippet,
    _compose_driver_cmd,
)
from services.remote_process import _BatchedRemoteProcess, _QueueLineIterator

_ARGV = ["python", "process_microscopy.py", "--input_dir", "/r/run-x/in"]


# ── the driver loop ──────────────────────────────────────────────────────────


def test_the_driver_runs_the_pipeline_once_per_batch_in_one_job():
    """The whole point. One launch, N pipeline runs inside it."""
    script = _batch_driver_snippet("/r/run-x", 4, _ARGV, "/r/run-x/.hb")
    assert '-le 4' in script
    # The argv appears once — it is a loop, not four launches unrolled.
    assert script.count("process_microscopy.py") == 1
    assert "batch-driver: all 4 batch(es) done" in script


def test_a_failed_batch_does_not_end_the_run():
    """Decided with the user: keep the node, run the rest, report the tally.
    An early exit here would hand back an allocation the plate still needs."""
    script = _batch_driver_snippet("/r/run-x", 3, _ARGV)
    assert 'exit "$_aw_rc"' not in script
    assert 'exit $_aw_rc' not in script
    assert "finished rc=" in script


def test_each_batch_replaces_the_input_directory_atomically():
    """The barrier. The pipeline for a batch must see that batch's zips and
    nothing else — the previous batch's are removed by replacing the whole
    directory rather than waiting for the Fetcher to delete them well by well,
    which nothing used to wait for."""
    script = _batch_driver_snippet("/r/run-x", 2, _ARGV)
    assert "rm -rf" in script and "mv " in script
    # And it happens before the pipeline, not after it.
    assert script.index("mv ") < script.index("process_microscopy.py")
    # A failed swap is reported as that batch failing, not as a run against a
    # stale input directory.
    assert "could not stage its input" in script


def test_one_batch_may_not_outlive_the_budget_meant_for_one_batch():
    """Without this a single wedged well holds the node for the whole run's
    scaled walltime — strictly worse than the per-batch launches this replaces,
    where a hang cost one batch's allocation and no more."""
    from services.remote_wait_snippets import BATCH_BUDGET_GRACE_S

    script = _batch_driver_snippet("/r/run-x", 2, _ARGV, "", 3600)
    assert f"timeout -k {BATCH_BUDGET_GRACE_S} 3600 python process_microscopy.py" in script
    # A launcher with no time limit gives no budget to enforce.
    assert "timeout" not in _batch_driver_snippet("/r/run-x", 2, _ARGV)


# ── the driver, run in bash ──────────────────────────────────────────────────


def _driver_dir(tmp_path, *, ready=(1,)):
    """A run directory with batch 1 staged and the given batches flagged ready."""
    run_dir = tmp_path / "run"
    (run_dir / "b1").mkdir(parents=True)
    for i in ready:
        (run_dir / BATCH_READY.format(i=i)).touch()
    return run_dir


def _alive(pid: int, *, within_s: float = 2.0) -> bool:
    """Whether *pid* is still running *within_s* from now. A killed orphan is a zombie until whatever adopted it
    reaps it, and a zombie runs nothing."""
    import time

    deadline = time.monotonic() + within_s
    while True:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError, IndexError):
            return False
        if state == "Z":
            return False
        if time.monotonic() > deadline:
            return True
        time.sleep(0.1)


def _hung_pipeline(tmp_path, *, behind_a_wrapper: bool):
    """A pipeline that treats SIGTERM as a suspend and never reaches the boundary it would stop at — a hung well.

    *behind_a_wrapper* puts it behind a `conda run`-like wrapper that itself dies on SIGTERM, which is what left the
    real pipeline running as an orphan in the review. Returns the argv and the file the hung process writes its pid to.
    """
    pid_file = tmp_path / "hung.pid"
    hung = tmp_path / "hung.sh"
    hung.write_text(f'trap "" TERM; echo $$ > {pid_file}; while :; do sleep 0.2; done\n')
    if not behind_a_wrapper:
        return ["bash", str(hung)], pid_file
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(f'bash {hung} &\nwait\n')
    return ["bash", str(wrapper)], pid_file


@pytest.mark.parametrize("behind_a_wrapper", [False, True], ids=["the pipeline", "a wrapper's orphan"])
def test_a_batch_over_its_time_budget_is_killed_and_says_so(tmp_path, monkeypatch, behind_a_wrapper):
    """`timeout N` sent SIGTERM and no more: the pipeline took it for a suspend and went on, and when a wrapper died
    on it instead the pipeline was orphaned and kept writing `out/` into the next batch. Measured in the review: a
    3 s budget still running at 15 s, the orphan with ppid 1."""
    import os
    import signal
    import subprocess
    import time

    import services.remote_commands as rc

    monkeypatch.setattr(rc, "BATCH_BUDGET_GRACE_S", 2)
    run_dir = _driver_dir(tmp_path)
    argv, pid_file = _hung_pipeline(tmp_path, behind_a_wrapper=behind_a_wrapper)
    script = rc._batch_driver_snippet(str(run_dir), 1, argv, "", 2)
    t0 = time.monotonic()
    try:
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        elapsed = time.monotonic() - t0
        pid = int(pid_file.read_text())
        assert not _alive(pid), "the hung pipeline outlived its batch"
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert "[remote] ERROR batch 1 exceeded its time budget (2s)" in out.stdout, out.stdout + out.stderr
    assert "all 1 batch(es) done" in out.stdout
    assert elapsed < 15, f"the batch held the node {elapsed:.0f} s past a 2 s budget"
    assert ("batch 1 finished rc=124" in out.stdout) is behind_a_wrapper or "finished rc=137" in out.stdout


def test_a_pipeline_that_ends_in_time_is_not_over_budget(tmp_path):
    import subprocess

    run_dir = _driver_dir(tmp_path)
    script = _batch_driver_snippet(str(run_dir), 1, ["true"], "", 30)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert "batch 1 finished rc=0" in out.stdout
    assert "exceeded" not in out.stdout and "outlived" not in out.stdout


#: A pipeline the shape of the real one at exit: a `spawn` process pool, whose `multiprocessing` resource tracker
#: outlives the main process by a few milliseconds, by design.
_SPAWN_POOL_PIPELINE = (
    "import multiprocessing as mp\n"
    "from concurrent.futures import ProcessPoolExecutor\n"
    "if __name__ == '__main__':\n"
    "    with ProcessPoolExecutor(1, mp_context=mp.get_context('spawn')) as ex:\n"
    "        print(ex.submit(abs, -3).result())\n")


def test_a_pool_s_resource_tracker_is_not_a_pipeline_that_outlived_its_batch(tmp_path):
    """Review D7, found by the localhost harness (``tests/test_a_remote_run_on_a_standin_cluster.py``): every clean
    batch of a real run printed "processes of its pipeline outlived it" and waited a second, because the driver's
    first ``kill -0`` of the batch's group landed while the pool's resource tracker was still exiting. A warning that
    fires on every batch hides the batch it is true of."""
    import subprocess
    import sys

    run_dir = _driver_dir(tmp_path)
    script = tmp_path / "pipeline.py"
    script.write_text(_SPAWN_POOL_PIPELINE)
    out = subprocess.run(["bash", "-c", _batch_driver_snippet(str(run_dir), 1, [sys.executable, str(script)], "", 600)],
                         capture_output=True, text=True, timeout=60)
    assert "batch 1 finished rc=0" in out.stdout, out.stdout + out.stderr
    assert "outlived" not in out.stdout, out.stdout


def test_a_process_that_really_outlives_its_batch_is_still_stopped_and_said(tmp_path, monkeypatch):
    """The other half of D7: the settle window only waits for a group that is emptying, it does not excuse one that
    is not."""
    import subprocess
    import time

    import services.remote_commands as rc

    monkeypatch.setattr(rc, "BATCH_BUDGET_GRACE_S", 2)
    run_dir = _driver_dir(tmp_path)
    pid_file = tmp_path / "left.pid"
    argv = ["bash", "-c", f"sleep 30 & echo $! > {pid_file}; exit 0"]
    t0 = time.monotonic()
    out = subprocess.run(["bash", "-c", rc._batch_driver_snippet(str(run_dir), 1, argv, "", 600)],
                         capture_output=True, text=True, timeout=60)
    assert "[remote] WARN batch 1: processes of its pipeline outlived it" in out.stdout, out.stdout
    assert not _alive(int(pid_file.read_text())), "the process left behind was not stopped"
    assert time.monotonic() - t0 < 15


def test_a_running_batch_says_it_is_still_running(tmp_path, monkeypatch):
    """Hours of silence mid-batch looked exactly like a dead stream; the driver ticks while a batch runs."""
    import re
    import subprocess

    import services.remote_commands as rc

    monkeypatch.setattr(rc, "MID_BATCH_WATCH_POLL_S", 1)
    monkeypatch.setattr(rc, "BATCH_TICK_S", 1)
    run_dir = _driver_dir(tmp_path)
    script = rc._batch_driver_snippet(str(run_dir), 1, ["sleep", "3.5"], "", 0)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    ticks = re.findall(r"\[remote\] batch-driver: batch 1 still running \((\d+)m\)", out.stdout)
    assert len(ticks) >= 2, out.stdout
    assert ticks[0] == "0"
    assert out.stdout.index("still running") < out.stdout.index("batch 1 finished rc=0")


def _fresh(path, ahead_s=120):
    import os
    import time

    path.touch()
    t = time.time() + ahead_s
    os.utime(path, (t, t))


def test_a_beating_app_s_slow_upload_is_waited_for(tmp_path, monkeypatch):
    """The upload deadline used to end a healthy run: six nanowell wells (~48 GB) need > 53 Mbit/s to arrive in
    two hours. Scaled: a 6 s deadline, a fresh heartbeat, nothing arrived — the driver must still be waiting."""
    import subprocess

    import services.remote_commands as rc

    monkeypatch.setattr(rc, "UPLOAD_WAIT_TIMEOUT_S", 6)
    run_dir = _driver_dir(tmp_path, ready=())
    heartbeat = run_dir / ".hb"
    _fresh(heartbeat)
    script = rc._batch_driver_snippet(str(run_dir), 1, ["true"], str(heartbeat), 0)
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=12)
    text = exc.value.stdout.decode() if isinstance(exc.value.stdout, bytes) else (exc.value.stdout or "")
    assert "never arrived" not in text


def test_with_no_heartbeat_to_vouch_for_the_app_the_deadline_still_stands(tmp_path, monkeypatch):
    """The control, and the backstop C6 keeps: nothing says the uploader is alive, so the node is given back."""
    import subprocess

    import services.remote_commands as rc

    monkeypatch.setattr(rc, "UPLOAD_WAIT_TIMEOUT_S", 6)
    run_dir = _driver_dir(tmp_path, ready=())
    script = rc._batch_driver_snippet(str(run_dir), 1, ["true"], "", 0)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 75
    assert "[remote] ERROR batch 1 never arrived" in out.stdout


def test_the_single_shot_upload_wait_waits_for_a_beating_app_too(tmp_path, monkeypatch):
    """The sibling: the single-shot job's wait for its upload sentinel had the same fixed deadline."""
    import subprocess

    import services.remote_wait_snippets as ws

    monkeypatch.setattr(ws, "UPLOAD_WAIT_TIMEOUT_S", 6)
    heartbeat = tmp_path / ".hb"
    _fresh(heartbeat)
    script = ws._wait_for_upload_snippet(str(tmp_path / "sentinel"), str(heartbeat)) + "echo started"
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=12)
    text = exc.value.stdout.decode() if isinstance(exc.value.stdout, bytes) else (exc.value.stdout or "")
    assert "never completed" not in text
    out = subprocess.run(["bash", "-c", ws._wait_for_upload_snippet(str(tmp_path / "sentinel"))],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 75 and "never completed" in out.stdout


def test_heartbeat_staleness_is_judged_by_the_clock_that_stamped_it(tmp_path):
    """The heartbeat's mtime is the file server's clock and `date` is the compute node's: an hour of skew between
    them read a fresh heartbeat as stale and gave the node back at every batch boundary."""
    import os
    import subprocess

    shims = tmp_path / "bin"
    shims.mkdir()
    real_date = subprocess.run(["bash", "-c", "command -v date"], capture_output=True, text=True).stdout.strip()
    (shims / "date").write_text(f'#!/bin/sh\necho $(( $({real_date} +%s) + 3600 ))\n')    # an hour ahead
    (shims / "date").chmod(0o755)
    run_dir = _driver_dir(tmp_path, ready=())
    heartbeat = run_dir / ".hb"
    heartbeat.touch()                                   # beating, by the filesystem's clock
    script = _batch_driver_snippet(str(run_dir), 1, ["true"], str(heartbeat), 0)
    env = {**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"}
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=8, env=env)
    text = exc.value.stdout.decode() if isinstance(exc.value.stdout, bytes) else (exc.value.stdout or "")
    assert "stopped responding" not in text, "a fresh heartbeat was read as stale through clock skew"


@pytest.mark.parametrize("flag,why", [
    (".aw_batch_2_skip", "nothing was held for the end-of-run re-anchoring"),
    (None, "the end-of-run re-anchoring was not flagged")])
def test_a_skipped_final_pass_says_it_was_skipped(tmp_path, flag, why):
    import subprocess

    run_dir = _driver_dir(tmp_path, ready=(1, 2))
    if flag:
        (run_dir / flag).touch()
    script = _batch_driver_snippet(str(run_dir), 1, ["true"], "", 0, final_pass=True)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert f"[remote] batch-driver: batch 2 skipped — {why}" in out.stdout, out.stdout
    assert "batch 2 starting" not in out.stdout


def test_the_driver_gives_the_node_back_when_the_app_goes_away():
    from services.remote_wait_snippets import UPLOAD_WAIT_TIMEOUT_S
    from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S

    script = _batch_driver_snippet("/r/run-x", 2, _ARGV, "/r/run-x/.hb")
    assert "stopped responding" in script
    assert str(UPLOAD_HEARTBEAT_STALE_S) in script
    assert "never arrived" in script
    assert str(UPLOAD_WAIT_TIMEOUT_S // 3) in script


def test_the_batch_number_is_a_shell_variable_that_actually_expands():
    """`_remote_quote` single-quotes, so a whole-path quote would leave the
    loop watching for a file literally named ".aw_batch_$_aw_i_ready" — and the
    braces matter too, since bare `$_aw_i_ready` names a different, unset
    variable, which would make every batch wait on the same empty path."""
    script = _batch_driver_snippet("/r/run-x", 2, _ARGV)
    assert BATCH_READY.format(i="${_aw_i}") in script
    assert BATCH_DONE.format(i="${_aw_i}") in script
    assert BATCH_READY.format(i="$_aw_i") not in script


def test_the_composed_command_still_carries_the_cancel_channels():
    script = _batch_driver_snippet("/r/run-x", 2, _ARGV)
    cmd = _compose_driver_cmd("srun -N1", "/r/run-x/run.pid", script)
    assert "srun -N1" in cmd
    assert "/r/run-x/run.pid.slurm" in cmd     # scancel reads this
    assert "echo $$ > " in cmd                 # kill -TERM reads this
    assert "held for every batch" in cmd
    # No launcher: nothing to hold, so it runs where ssh lands.
    assert "srun" not in _compose_driver_cmd("", "/r/run-x/run.pid", script)


# ── the walltime a held allocation forces ────────────────────────────────────


@pytest.mark.parametrize("spec,seconds", [
    ("5", 300),                 # MM
    ("5:30", 330),              # MM:SS
    ("1:02:03", 3723),          # HH:MM:SS
    ("2-3", 183600),            # DD-HH
    ("1-2:03", 93780),          # DD-HH:MM
    ("1-2:03:04", 93784),       # DD-HH:MM:SS
])
def test_every_slurm_time_format_is_understood(spec, seconds):
    """All six, because a launcher that works on the cluster must not be
    rejected here — and because the forms are genuinely ambiguous: `5` is five
    minutes while `1-5` is a day and five hours."""
    assert parse_slurm_time(spec) == seconds


def test_junk_is_not_guessed_at():
    for spec in ("", "later", "1:2:3:4", "UNLIMITED", "-5"):
        assert parse_slurm_time(spec) is None


def test_the_walltime_covers_the_batches_and_the_transfers_between_them():
    """It sized one batch's compute; one allocation now covers the plate — and
    the node also sits through every upload after the first, because the driver
    waits for each batch inside the allocation. Budgeting only the compute puts
    the walltime expiry somewhere in the last batch of any run whose transfers
    are not free."""
    out, note = scale_launcher_time("srun --partition=gpu --time=4:00:00", 3)
    assert "--time=16:00:00" in out, "3 batches + 1 batch of transfer budget"
    assert "scaled" in note and "4:00:00" in note
    assert "transfers between them" in note, "a guess has to say it is one"

    # The allowance is a parameter, not a constant baked into the arithmetic.
    bare, _ = scale_launcher_time("srun --time=4:00:00", 3, transfer_allowance=0)
    assert "--time=12:00:00" in bare


def test_scaling_is_clamped_and_says_so():
    out, note = scale_launcher_time("srun --time=4:00:00", 12)
    assert f"--time={render_slurm_time(MAX_HELD_ALLOCATION_S)}" in out
    assert "clamped" in note


def test_a_launcher_without_a_time_limit_is_returned_untouched():
    """The cluster's own default applies, and inventing a number would be worse
    than inheriting theirs."""
    for launcher in ("srun --partition=gpu", "", "srun --gres=gpu:1"):
        out, note = scale_launcher_time(launcher, 8)
        assert out == launcher
        assert note == ""


def test_the_short_form_is_read_too():
    assert launcher_time_seconds("srun -t 2:00:00 --mem=8G") == 7200
    assert launcher_time_seconds("srun --time=30") == 1800
    assert launcher_time_seconds("srun --partition=gpu") == 0


def test_an_odd_flag_survives_the_round_trip():
    """`--constraint=a|b` has to reach the scheduler literally."""
    out, _ = scale_launcher_time("srun --constraint='a|b' --time=1:00:00", 2)
    assert "a|b" in out
    assert "--time=03:00:00" in out    # 2 batches + the transfer allowance


# ── the local side of the handshake ──────────────────────────────────────────


class _FakeProc:
    """A Popen-shaped driver whose stdout a test writes on cue."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.returncode = 0
        self.terminated = False
        self._done = False
        self.pid = 4242

    def say(self, line):
        self._q.put(line)

    def batch_finished(self, owner, i, rc=0):
        """What the driver's stream holds when batch *i* of *owner* (the batched process) ends.

        A real batch that exits 0 has first printed ``Well <W> — done.`` for every
        well it was sent, and the batch loop now checks that it did
        (``remote_batch_ledger.unreported_after_success``). Written out when the
        reader gets to it, because a test queues this before the loop has sliced
        the plate into batches.
        """
        def lines():
            batches = getattr(owner, "_batches", None) or ()
            wells = [z.stem for z in batches[i - 1]] if rc == 0 and i <= len(batches) else []
            return [f"Well {w} — done.\n" for w in wells] + [f"[remote] batch-driver: batch {i} finished rc={rc}\n"]
        self._q.put(lines)

    def finish(self):
        self._done = True
        self._q.put(None)

    @property
    def stdout(self):
        def _iter():
            while True:
                item = self._q.get()
                if item is None:
                    return
                if callable(item):
                    yield from item()
                    continue
                yield item
        return _iter()

    def poll(self):
        return self.returncode if self._done else None

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        """What a real ssh does when it is signalled: it exits, and its stdout ends.

        Recording the call and leaving the stream open made every test of a run
        the app ended itself — a hand-off it gave up on, a driver it released —
        stop one step short of what follows: the reader seeing the stream end
        with batches unreported, which is where a lost driver used to be taken
        for the user's Stop.
        """
        self.terminated = True
        if not self._done:
            self.finish()

    def kill(self):
        self.terminate()


def _batched(tmp_path, wells, batch_size=1):
    """A `_BatchedRemoteProcess` with everything remote stubbed out."""
    p = object.__new__(_BatchedRemoteProcess)
    zips = []
    for well in wells:
        z = tmp_path / f"{well}.zip"
        z.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        zips.append(z)
    p._zipped = types.SimpleNamespace(well_zips=zips, incremental=False,
                                      mode=None)
    p._local_output_dir = tmp_path
    p._pipeline_argv = ["--csv_prefix", "measurements"]
    p._batch_size = batch_size
    p._line_q = queue.Queue()
    p._lines = _QueueLineIterator(p._line_q)       # `stdout`, as the runner reads it
    p._log = lambda m: p._logged.append(m)
    p._logged = []
    p._returncode = None
    p._stop = threading.Event()
    p._suspending = threading.Event()
    p._current_proc_lock = threading.Lock()
    p._current_proc = None
    p._batch_events = {}
    p._batch_rc = {}
    p._stream_ended = threading.Event()
    p._driver_lost = threading.Event()
    p._n_batches = 0
    p._owns_allocation = False
    p._reader = None
    p._heartbeat = None
    p._run_id = "run-test"
    p._output_filter = None
    p._progress = None
    p._fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(well_count_total=0, fetched_wells=set(), nanowell=False),
        observe=lambda line: None,
        flush_and_join=lambda *a, **k: None,
        cancel=lambda: p._cancelled.append(True),
    )
    p._cancelled = []
    p._run_local_auto_threshold = lambda: None
    p._auto_threshold_off_by_user = lambda: True
    return p


def _stub_runner(p, proc, ordering):
    """Give *p* a runner whose remote calls only record what was asked."""
    def ssh_run(argv, *, check, **kw):
        ordering.append(" ".join(str(a) for a in argv[:2]))
        return types.SimpleNamespace(stdout="", returncode=0)

    def upload_subset(zips, dest, **kw):
        ordering.append(f"upload:{dest.rsplit('/', 1)[-1]}")
        return (0, 0.0, 0)

    p._runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(_ssh_run=ssh_run,
                                     # a nanowell run's hold bookkeeping (`remote_final_pass`): nothing held
                                     _ssh_script=lambda script, *, check: types.SimpleNamespace(stdout="",
                                                                                                returncode=0),
                                     upload_subset=upload_subset),
        _run=types.SimpleNamespace(
            remote_input_dir="/r/run-test/in",
            remote_output_dir="/r/run-test/out",
            remote_pidfile="/r/run-test/run.pid"),
        host=types.SimpleNamespace(
            remote_staging_dir="/r", compute_launcher="srun --time=1:00:00", launch_command="srun --time=1:00:00",
            remote_scratch_dir="", ssh_argv=lambda **kw: ["ssh", "h"]),
        env_selector=("name", "allwell"),
        _build_remote_argv=lambda *a, **k: ["python", "pm.py"],
        _sweep_orphaned_allocation=lambda *a, **k: None,
        _claim_run_dirs=lambda *a, **k: "",
    )
    return proc


def test_one_allocation_serves_every_batch(tmp_path, monkeypatch):
    """The assertion the whole change exists for: three batches, one ssh."""
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)

    spawns = []
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: spawns.append(argv) or proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in (1, 2, 3):
        proc.batch_finished(p, i)
    proc.finish()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert len(spawns) == 1, f"{len(spawns)} launches for 3 batches"
    assert p._returncode == 0
    assert ordering.count("upload:b1") == 1
    assert [o for o in ordering if o.startswith("upload:")] == [
        "upload:b1", "upload:b2", "upload:b3"]


def _drive(tmp_path, monkeypatch, wells, batch_size, script):
    """Run the batch loop over *wells*; *script(proc, p)* plays the driver. Returns the process once it ends."""
    proc = _FakeProc()
    p = _batched(tmp_path, wells, batch_size=batch_size)
    _stub_runner(p, proc, [])
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    script(proc, p)
    proc.finish()
    worker.join(timeout=20)
    assert not worker.is_alive()
    return p


def test_a_failed_batch_is_tallied_with_what_it_really_lost(tmp_path, monkeypatch):
    """Batch 2 fails because one of its two wells came out partial: the tally says that, not that both are missing.

    The tally used to say "Wells from a failed batch are missing from the output" whatever the batch had done — here
    A03 finished whole, and A04 has its output less some FOV-groups."""
    def driver(proc, p):
        proc.batch_finished(p, 1)
        proc.say("Well A03 — done.\n")
        proc.say("2026-10-28 10:00:00  WARNING   Well A04 — PARTIAL: 2 of 9 FOV-group(s) FAILED (F003, F007).\n")
        proc.say("Well A04 — done.\n")
        proc.say("[remote] batch-driver: batch 2 finished rc=4\n")
        proc.batch_finished(p, 3)

    p = _drive(tmp_path, monkeypatch, ["A01", "A02", "A03", "A04", "A05", "A06"], 2, driver)

    assert p._returncode == 4, "a failed batch has to reach the run's own code"
    tally = [m for m in p._logged if "batch(es) failed" in m]
    assert tally == ["[remote] ERROR 1 of 3 batch(es) failed — batch 2 (exit 4): A04 partial. Every other well "
                     "completed.\n"], p._logged
    assert "continuing with the remaining batches" in "".join(p._logged)
    assert p.failure_lines == (tally[0].strip(),), "the run's headline does not carry the tally"


def test_a_failed_batch_names_the_wells_that_never_finished(tmp_path, monkeypatch):
    def driver(proc, p):
        proc.batch_finished(p, 1)
        proc.say("2026-10-28 10:00:00  ERROR     FAILED well A03: out of memory.\n")
        proc.say("Well A04 — done.\n")
        proc.say("[remote] batch-driver: batch 2 finished rc=1\n")

    p = _drive(tmp_path, monkeypatch, ["A01", "A02", "A03", "A04"], 2, driver)

    assert p._returncode == 1
    assert ("[remote] ERROR 1 of 2 batch(es) failed — batch 2 (exit 1): A03 not finished. Every other well "
            "completed.\n") in p._logged, p._logged


def test_a_batch_stopped_for_its_time_budget_is_reported_as_that(tmp_path, monkeypatch):
    """Not as "pipeline exited 124", and not as a suspend: the driver says the budget ran out, and the tally names it."""
    def driver(proc, p):
        proc.batch_finished(p, 1)
        proc.say("Well A03 — done.\n")
        proc.say("[remote] ERROR batch 2 exceeded its time budget (3600s) — its pipeline was stopped; the wells it "
                 "had not finished are missing\n")
        proc.say("[remote] batch-driver: batch 2 finished rc=124\n")

    p = _drive(tmp_path, monkeypatch, ["A01", "A02", "A03", "A04"], 2, driver)

    assert p._returncode == 124
    log = "".join(p._logged)
    assert "[remote] batch 2 exceeded its time budget and was stopped; continuing with the remaining batches" in log
    assert ("[remote] ERROR 1 of 2 batch(es) failed — batch 2 (exit 124): exceeded its time budget; A04 not "
            "finished. Every other well completed.") in log


def test_a_batch_that_exits_0_without_its_wells_is_not_counted_complete(tmp_path, monkeypatch):
    """A batch the driver swapped in before its upload finished (a stale `ready` flag) runs what had arrived and
    exits 0. Two of its three wells never said a word, and the run used to report success over them."""
    def driver(proc, p):
        proc.say("Well A01 — done.\n")
        proc.say("[remote] batch-driver: batch 1 finished rc=0\n")
        proc.batch_finished(p, 2)

    p = _drive(tmp_path, monkeypatch, ["A01", "A02", "A03", "A04"], 3, driver)

    assert p._returncode == 1, "a batch whose wells never reported was counted complete"
    log = "".join(p._logged)
    assert ("[remote] ERROR batch 1 exited 0, but 2 of its 3 well(s) never reported finished — A02, A03; their "
            "output is missing") in log
    assert "batch 1 (exit 0): A02, A03 not finished" in log
    assert "batch 2" not in log.split("batch(es) failed")[1], "batch 2 reported every well and is not a failure"


def test_a_well_the_pipeline_skipped_is_accounted_for(tmp_path, monkeypatch):
    """The host's own skip (its output already exists there) says so, and is not a missing well."""
    def driver(proc, p):
        proc.say("Well A01 — done.\n")
        proc.say("2026-10-28 10:00:00  INFO      SKIP well A02 — output already exists. Use --force to reprocess.\n")
        proc.say("[remote] batch-driver: batch 1 finished rc=0\n")

    p = _drive(tmp_path, monkeypatch, ["A01", "A02"], 2, driver)

    assert p._returncode == 0, p._logged
    assert "ERROR" not in "".join(p._logged)


def test_a_suspend_finishes_the_batch_in_flight_and_sends_no_more(
        tmp_path, monkeypatch):
    """Batch 1 is running when Suspend lands; batches 2 and 3 are never uploaded.

    The driver stops of its own accord once it sees the request file
    (`_batch_driver_snippet`), so its stream ends with batches unreported — the
    shape a lost node also has. The run must end 0, as a suspend, with the
    Fetcher left to bring batch 1's wells home.
    """
    import time

    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)
    closed = []
    p._runner.close_remote_job = lambda **kw: closed.append(True)
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while not any("ready" in o for o in ordering) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert any("ready" in o for o in ordering), ordering
    p.note_suspend_requested()
    # The driver has exited by the time its last lines are read — set before
    # they are said, so the release never sees it still running by accident.
    proc._done = True
    proc.batch_finished(p, 1)
    proc.say("[remote] batch-driver: suspended — no further batches\n")
    proc.finish()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert [o for o in ordering if o.startswith("upload:")] == ["upload:b1"]
    assert p._returncode == 0
    assert not p._driver_lost.is_set()
    assert p._cancelled == [], "the downloads of the finished batch were cancelled"
    assert "not sent" in "".join(p._logged)
    assert not proc.terminated, "a driver that ended on its own was killed"
    assert closed == [], "a job that ended on its own was cancelled"


def test_a_suspend_during_a_batch_upload_ends_the_upload(tmp_path, monkeypatch):
    """A batch still uploading when Suspend lands is abandoned, not finished.

    It would be thrown away unrun either way: the loop sends no batch after a
    suspend. Watching only `_stop`, the upload ran to the end first — the whole
    transfer paid for a batch that was then discarded.
    """
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, ordering)
    uploads = []

    def upload(zips, dest, *, stop_event=None, **kw):
        uploads.append(dest.rsplit("/", 1)[-1])
        if len(uploads) == 1:
            return (0, 0.0, 0)
        p.note_suspend_requested()          # Suspend, mid-transfer of batch 2
        # A transfer far longer than the test waits: only an upload that sees
        # the suspend comes back in time.
        stopped = stop_event.wait(timeout=30)
        return (0, 0.0, 20 if stopped else 0)

    p._runner._cache.upload_subset = upload
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.batch_finished(p, 1)
    worker.join(timeout=5)
    interrupted = not worker.is_alive()
    proc.finish()

    assert interrupted, "the upload was not interrupted by the suspend"
    assert uploads == ["b1", "b2"]
    assert p._returncode == 0
    assert not any("upload failed" in m for m in p._logged)
    assert "not sent" in "".join(p._logged)
    assert not any("b2_ready" in o or "_2_ready" in o for o in ordering), ordering


def test_a_suspend_while_the_job_is_queued_gives_the_allocation_back(
        tmp_path, monkeypatch):
    """Suspend lands during batch 1's upload, while the job is still queued.

    The loop ends with nothing failed, so the run's code is 0 — and a code of 0
    read as "the driver exited on its own" left the ssh holding the queue
    position alive. The job would take the node whenever the queue reached it,
    only to see the request file and hand it straight back.
    """
    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, [])
    closed = []
    p._runner.close_remote_job = lambda **kw: closed.append(True)

    def upload(zips, dest, *, stop_event=None, **kw):
        p.note_suspend_requested()
        return (0, 0.0, 20)                 # the transfer the suspend cut short

    p._runner._cache.upload_subset = upload
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    worker.join(timeout=20)
    proc.finish()

    assert not worker.is_alive()
    assert p._returncode == 0
    assert proc.terminated, "the queued driver was left holding its place"
    assert closed == [True], "the scheduler job was not cancelled"


def test_a_suspended_plate_gets_no_thresholds_from_its_fraction(tmp_path, monkeypatch):
    """The end-of-run auto-threshold pass is for a finished plate.

    Run over the wells a suspend left, it wrote cutoffs from a fraction of the
    plate into the sidecar the resumed run then starts from.
    """
    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, [])
    ran = []
    p._auto_threshold_off_by_user = lambda: False
    p._run_local_auto_threshold = lambda: ran.append(True)
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    p.note_suspend_requested()
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)
    assert p._returncode == 0
    assert ran == []


def test_the_markers_never_reach_the_log(tmp_path, monkeypatch):
    """They are this loop's handshake with the driver; `ProgressTracker` has no
    reason to see them, and a stray one in the log reads as pipeline output."""
    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.say("Well A01 — done.\n")
    proc.batch_finished(p, 1)
    proc.finish()
    worker.join(timeout=20)

    lines = []
    while not p._line_q.empty():
        item = p._line_q.get()
        if item is not None:
            lines.append(item)
    assert any("Well A01" in ln for ln in lines)
    assert not any("batch-driver" in ln for ln in lines)


def test_a_dead_driver_ends_the_run_as_a_failure_and_lets_the_downloads_drain(tmp_path, monkeypatch):
    """The stream ending with batches unreported, and no Stop pressed, is the allocation going away.

    It used to set the stop event — which is the runner's own `_stop_requested`,
    so it was reported as the user's Stop — and cancel the Fetcher, leaving the
    finished batch's wells on the remote. Now it is a failure that says so, the
    caller's Stop is untouched, and the downloads finish.
    """
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.say("Well A01 — done.\n")
    proc.batch_finished(p, 1)
    proc.finish()                       # the allocation is gone
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert p._returncode == 1
    assert p._driver_lost.is_set()
    assert not p._stop.is_set(), "a lost driver set the caller's Stop"
    assert p._cancelled == [], "the Fetcher was cancelled with finished wells still on the remote"
    assert "upload:b3" not in ordering, "kept sending batches to a dead driver"
    assert any(m.startswith("[remote] ERROR the allocation ended") for m in p._logged), p._logged


def _blocking_upload(p, ordering, uploading):
    def upload(zips, dest, *, stop_event=None, **kw):
        ordering.append(f"upload:{dest.rsplit('/', 1)[-1]}")
        uploading.set()
        # What the real rsync wrapper does: transfer until told to stop, then
        # report the non-zero code of the interrupted child.
        assert stop_event is not None, "the upload cannot see the Stop at all"
        stop_event.wait(timeout=20)
        return (0, 0.0, 0 if not stop_event.is_set() else 20)
    p._runner._cache.upload_subset = upload


def test_a_stop_during_an_upload_ends_the_upload(tmp_path, monkeypatch):
    """Stop arrives as the event (`PipelineRunner.stop` sets it first) and then the ssh's death.

    The worker is inside an rsync at the time; ``upload_subset`` watches the
    event, so the transfer ends rather than pushing wells at a node the Stop
    just released, and it reads as the user's Stop rather than as a fault.
    """
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)
    uploading = threading.Event()
    _blocking_upload(p, ordering, uploading)
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    assert uploading.wait(timeout=20), "batch 1 never started uploading"
    p._stop.set()
    proc.finish()                       # the process-group kill, seen as EOF

    worker.join(timeout=20)
    assert not worker.is_alive(), "the upload outlived the Stop"
    assert [o for o in ordering if o.startswith("upload:")] == ["upload:b1"]
    assert p._returncode == 130
    assert not p._driver_lost.is_set()
    assert not any("upload failed" in m for m in p._logged), (
        "an interrupted upload is not a fault to report as one")
    assert any("upload interrupted" in m for m in p._logged)


def test_a_driver_lost_during_an_upload_ends_the_upload(tmp_path, monkeypatch):
    """The allocation ends while a batch is uploading: the transfer stops, as a failure, without the caller's Stop."""
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)
    uploading = threading.Event()
    _blocking_upload(p, ordering, uploading)
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    assert uploading.wait(timeout=20), "batch 1 never started uploading"
    proc.finish()                       # the node went away

    worker.join(timeout=20)
    assert not worker.is_alive(), "the upload outlived the allocation"
    assert p._returncode == 1
    assert not p._stop.is_set()
    assert p._cancelled == []
    assert [o for o in ordering if o.startswith("upload:")] == ["upload:b1"]
    assert any(m.startswith("[remote] ERROR the allocation ended while batch 1/3 was uploading")
               for m in p._logged), p._logged
    assert not any("upload failed" in m for m in p._logged)


def test_handshake_files_that_cannot_be_cleared_stop_the_run_before_it_launches(tmp_path, monkeypatch):
    """A stale `ready` flag that survives its `rm` lets the driver swap in a half-uploaded batch and report it done;
    the `rm`'s result used to be discarded."""
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, ordering)

    def ssh_run(argv, *, check, **kw):
        ordering.append(" ".join(str(a) for a in argv[:2]))
        rc = 1 if argv[0] == "rm" and any(str(a).endswith("_ready") for a in argv) else 0
        return types.SimpleNamespace(stdout="", stderr="rm: cannot remove: Permission denied", returncode=rc)

    p._runner._cache._ssh_run = ssh_run
    spawns = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: spawns.append(argv) or proc)

    p._run_batches()

    assert p._returncode == 1
    assert not spawns, "a driver was launched over stale handshake files"
    assert not any(o.startswith("upload") for o in ordering)
    assert ("[remote] ERROR could not clear the handshake files a previous run of this dataset left (ssh exit 1: "
            "rm: cannot remove: Permission denied); stopping before anything is launched") in "".join(p._logged)
    assert p.failure_lines and "could not clear the handshake files" in p.failure_lines[0]


def test_a_stop_before_the_first_batch_reaches_nothing_remote(tmp_path):
    """Ordering that is load-bearing twice: no allocation should be left
    behind, and the object is built without a `_runner` in this state."""
    p = _batched(tmp_path, ["A01", "A02"])
    p._stop.set()
    p._run_batches()
    assert p._returncode == 130


def test_a_second_session_on_the_same_plate_does_not_clear_the_first_s_input(
        tmp_path, monkeypatch):
    """`run_id` is deterministic in (input, arguments), so two sessions running
    the same plate land on the same remote run directory — which `_start_driver`
    empties before it launches. Emptying one that is in use takes a running
    pipeline's input away mid-run, so the second session stops instead."""
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02"])
    _stub_runner(p, proc, ordering)
    from services.remote_sweep import _refusal
    p._runner._claim_run_dirs = lambda *a, **k: _refusal(
        "owned", ["4242"], "this dataset's run directory")

    spawns = []
    monkeypatch.setattr("subprocess.Popen",
                        lambda argv, **kw: spawns.append(argv) or proc)

    p._run_batches()

    assert not spawns, "launched a driver into somebody else's run directory"
    assert not any(o.startswith("rm") for o in ordering), "cleared it anyway"
    assert not any(o.startswith("upload") for o in ordering)
    assert p._returncode == 1
    assert "already running this exact dataset" in "".join(p._logged)
    # And nothing of ours is on the scheduler, so nothing may be cancelled.
    assert p._owns_allocation is False
