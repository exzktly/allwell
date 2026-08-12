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

import pytest

from services.remote_config import (
    MAX_HELD_ALLOCATION_S,
    launcher_time_seconds,
    parse_slurm_time,
    render_slurm_time,
    scale_launcher_time,
)
from services.remote_runner import (
    BATCH_DONE,
    BATCH_READY,
    _batch_driver_snippet,
    _BatchedRemoteProcess,
    _compose_driver_cmd,
)

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
    script = _batch_driver_snippet("/r/run-x", 2, _ARGV, "", 3600)
    assert "timeout 3600 python process_microscopy.py" in script
    # A launcher with no time limit gives no budget to enforce.
    assert "timeout" not in _batch_driver_snippet("/r/run-x", 2, _ARGV)


def test_the_driver_gives_the_node_back_when_the_app_goes_away():
    from services.remote_runner import (
        UPLOAD_HEARTBEAT_STALE_S,
        UPLOAD_WAIT_TIMEOUT_S,
    )

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


def test_the_walltime_is_scaled_by_the_batch_count():
    """It sized one batch; one allocation now covers the plate."""
    out, note = scale_launcher_time("srun --partition=gpu --time=4:00:00", 3)
    assert "--time=12:00:00" in out
    assert "scaled" in note and "4:00:00" in note


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
    assert "--time=02:00:00" in out


# ── the local side of the handshake ──────────────────────────────────────────


class _FakeProc:
    """A Popen-shaped driver whose stdout a test writes on cue."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.returncode = 0
        self.terminated = False
        self.pid = 4242

    def say(self, line):
        self._q.put(line)

    def finish(self):
        self._q.put(None)

    @property
    def stdout(self):
        def _iter():
            while True:
                item = self._q.get()
                if item is None:
                    return
                yield item
        return _iter()

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True


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
    p._log = lambda m: p._logged.append(m)
    p._logged = []
    p._returncode = None
    p._stop = threading.Event()
    p._current_proc_lock = threading.Lock()
    p._current_proc = None
    p._batch_events = {}
    p._batch_rc = {}
    p._stream_ended = threading.Event()
    p._reader = None
    p._heartbeat = None
    p._run_id = "run-test"
    p._output_filter = None
    p._progress = None
    p._fetcher = types.SimpleNamespace(
        run=types.SimpleNamespace(well_count_total=0, fetched_wells=set()),
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
    def ssh_run(argv, *, check):
        ordering.append(" ".join(str(a) for a in argv[:2]))
        return types.SimpleNamespace(stdout="", returncode=0)

    def upload_subset(zips, dest, **kw):
        ordering.append(f"upload:{dest.rsplit('/', 1)[-1]}")
        return (0, 0.0, 0)

    p._runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(_ssh_run=ssh_run,
                                     upload_subset=upload_subset),
        _run=types.SimpleNamespace(
            remote_input_dir="/r/run-test/in",
            remote_output_dir="/r/run-test/out",
            remote_pidfile="/r/run-test/run.pid"),
        host=types.SimpleNamespace(
            remote_staging_dir="/r", compute_launcher="srun --time=1:00:00",
            ssh_argv=lambda **kw: ["ssh", "h"]),
        env_selector=("name", "allwell"),
        _build_remote_argv=lambda *a, **k: ["python", "pm.py"],
        _sweep_orphaned_allocation=lambda *a, **k: None,
    )
    return proc


def test_one_allocation_serves_every_batch(tmp_path, monkeypatch):
    """The assertion the whole change exists for: three batches, one ssh."""
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)

    spawns = []
    monkeypatch.setattr("services.remote_runner.subprocess.Popen",
                        lambda argv, **kw: spawns.append(argv) or proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    for i in (1, 2, 3):
        proc.say(f"[remote] batch-driver: batch {i} finished rc=0\n")
    proc.finish()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert len(spawns) == 1, f"{len(spawns)} launches for 3 batches"
    assert p._returncode == 0
    assert ordering.count("upload:b1") == 1
    assert [o for o in ordering if o.startswith("upload:")] == [
        "upload:b1", "upload:b2", "upload:b3"]


def test_a_failed_batch_is_tallied_and_the_run_continues(tmp_path, monkeypatch):
    proc = _FakeProc()
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, [])
    monkeypatch.setattr("services.remote_runner.subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.say("[remote] batch-driver: batch 1 finished rc=0\n")
    proc.say("[remote] batch-driver: batch 2 finished rc=4\n")
    proc.say("[remote] batch-driver: batch 3 finished rc=0\n")
    proc.finish()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert p._returncode == 4, "a failed batch has to reach the run's own code"
    tally = "".join(p._logged)
    assert "1 of 3 batch(es) failed" in tally
    assert "continuing with the remaining batches" in tally


def test_the_markers_never_reach_the_log(tmp_path, monkeypatch):
    """They are this loop's handshake with the driver; `ProgressTracker` has no
    reason to see them, and a stray one in the log reads as pipeline output."""
    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    monkeypatch.setattr("services.remote_runner.subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.say("Well A01 — done.\n")
    proc.say("[remote] batch-driver: batch 1 finished rc=0\n")
    proc.finish()
    worker.join(timeout=20)

    lines = []
    while not p._line_q.empty():
        item = p._line_q.get()
        if item is not None:
            lines.append(item)
    assert any("Well A01" in ln for ln in lines)
    assert not any("batch-driver" in ln for ln in lines)


def test_a_dead_driver_stops_the_run_and_the_downloads(tmp_path, monkeypatch):
    """A Stop arrives as a process-group kill, which sets none of this object's
    state — so the stream ending is the only signal, and without acting on it
    the run would carry on downloading as though nothing had happened."""
    proc = _FakeProc()
    ordering: list[str] = []
    p = _batched(tmp_path, ["A01", "A02", "A03"])
    _stub_runner(p, proc, ordering)
    monkeypatch.setattr("services.remote_runner.subprocess.Popen",
                        lambda argv, **kw: proc)

    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.say("[remote] batch-driver: batch 1 finished rc=0\n")
    proc.finish()                       # the allocation is gone
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert p._returncode not in (0, None)
    assert p._cancelled, "the Fetcher kept downloading after the driver died"
    assert "upload:b3" not in ordering, "kept sending batches to a dead driver"


def test_a_stop_before_the_first_batch_reaches_nothing_remote(tmp_path):
    """Ordering that is load-bearing twice: no allocation should be left
    behind, and the object is built without a `_runner` in this state."""
    p = _batched(tmp_path, ["A01", "A02"])
    p._stop.set()
    p._run_batches()
    assert p._returncode == 130
