"""The threads inside a nanowell worker: results in order, one segmenter call at a time, budgets from the run.

``pipeline.nanowell_run.run_well`` locates a well's timepoints and measures a timepoint's tiles on the worker's
threads (``pipeline/nanowell_parallel.py``). What makes that safe is held here without rendering anything: results
come back in the items' order whatever order the threads finish in, a segmenter behind the worker's lock never has
two calls in flight, and the thread and memory budgets are read from what the run told the worker.
"""

from __future__ import annotations

import threading
import time

import pytest

from pipeline import nanowell_parallel as par
from pipeline import nanowell_run as nr


def test_results_come_back_in_the_items_order_whatever_order_the_threads_finish_in():
    finished = []

    def slow_first(i):
        time.sleep(0.05 * (4 - i))            # item 0 finishes last
        finished.append(i)
        return i * 10

    assert par.ordered_map(slow_first, range(4), threads=4) == [0, 10, 20, 30]
    assert finished != [0, 1, 2, 3]           # the threads did finish out of order: the fixture can tell


def test_one_thread_is_the_serial_loop_on_the_calling_thread():
    me = threading.get_ident()
    assert par.ordered_map(lambda i: threading.get_ident(), range(3), threads=1) == [me] * 3


def test_a_failing_item_raises_where_the_loop_would_have_after_the_earlier_results():
    def fails_on_one(i):
        if i == 1:
            raise ValueError("item 1")
        return i

    got = []
    with pytest.raises(ValueError, match="item 1"):
        for value in par.ordered_results(fails_on_one, range(3), threads=3):
            got.append(value)
    assert got == [0]


def test_a_serialised_segmenter_never_has_two_calls_in_flight_and_keeps_its_attributes():
    state = {"in": 0, "most": 0}
    lock = threading.Lock()

    def touch():
        with lock:
            state["in"] += 1
            state["most"] = max(state["most"], state["in"])
        time.sleep(0.02)
        with lock:
            state["in"] -= 1

    def segment(image, lowhigh=None, max_size_fraction=None):
        touch()
        return image

    def many(images, lowhigh, max_size_fraction=None):
        touch()
        return images

    segment.accepts_range = True
    segment.accepts_size_cap = True
    segment.segment_many = many
    locked = par.serialised(segment, threading.Lock())
    assert locked.accepts_range and locked.accepts_size_cap
    par.ordered_map(lambda i: locked(i) if i % 2 else locked.segment_many([i], (0, 1)), range(8), threads=8)
    assert state["most"] == 1
    # Without the lock the same calls overlap, so the count above measures the lock.
    state["most"] = 0
    par.ordered_map(lambda i: segment(i), range(8), threads=8)
    assert state["most"] > 1


def test_a_segmenter_without_the_batch_or_range_attributes_gains_none():
    locked = par.serialised(lambda image: image, threading.Lock())
    assert not hasattr(locked, "segment_many") and not hasattr(locked, "accepts_range")
    assert locked(3) == 3


@pytest.mark.parametrize("environ, expected", [
    ({par.THREADS_ENV: "3", "OMP_NUM_THREADS": "7"}, 3),     # the override wins
    ({"OMP_NUM_THREADS": "2"}, 2),                           # else the worker's thread count
    ({par.THREADS_ENV: "zero", "OMP_NUM_THREADS": "2"}, 2),  # an unreadable override is passed over
    ({par.THREADS_ENV: "0"}, 1),                             # not positive: passed over, then nothing: serial
    ({}, 1),
])
def test_the_thread_budget_is_what_the_run_gave_the_worker(environ, expected):
    assert par.thread_budget(environ) == expected


def test_the_thread_budget_never_exceeds_the_cpus_the_process_may_use(monkeypatch):
    monkeypatch.setattr(par.os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)
    monkeypatch.setattr(par.os, "cpu_count", lambda: 64)
    assert par.thread_budget({par.THREADS_ENV: "64"}) == 2          # a Slurm job's two cores, not the node's 64


def test_a_stopped_iteration_leaves_nothing_running_and_starts_nothing_more():
    started, finished = [], []

    def slow(i):
        started.append(i)
        time.sleep(0.05)
        finished.append(i)
        return i

    results = par.ordered_results(slow, range(20), threads=3)
    assert next(results) == 0
    results.close()                                   # the caller stops (or raises) after the first result
    assert sorted(finished) == sorted(started)        # what had started has finished
    assert len(started) <= 4                          # and what had not was never started


def test_no_more_items_run_at_once_than_asked_even_on_a_larger_pool():
    par.ordered_map(lambda i: i, range(8), threads=8)               # the process's pool has grown to 8
    state = {"in": 0, "most": 0}
    lock = threading.Lock()

    def counted(i):
        with lock:
            state["in"] += 1
            state["most"] = max(state["most"], state["in"])
        time.sleep(0.02)
        with lock:
            state["in"] -= 1

    par.ordered_map(counted, range(12), threads=2)
    assert state["most"] == 2


def test_the_same_threads_serve_every_call():
    """Fresh threads for every timepoint each opened their own archive handles and Torch state; the pool is the
    process's, so successive calls share its threads."""
    pool = par._pool(3)
    seen = set()
    for _ in range(4):
        seen |= set(par.ordered_map(lambda i: (time.sleep(0.01), threading.get_ident())[1], range(6), threads=3))
    assert par._pool(3) is pool and len(seen) <= par._POOL_SIZE
    assert threading.get_ident() not in seen


def test_a_call_from_the_pool_s_own_threads_runs_serially_instead_of_waiting_on_itself():
    inner = par.ordered_map(lambda i: par.ordered_map(lambda j: i * 10 + j, range(3), threads=3), range(4), threads=4)
    assert inner == [[i * 10 + j for j in range(3)] for i in range(4)]


def test_a_segmenter_wrapped_twice_does_not_wait_on_itself():
    twice = par.serialised(par.serialised(lambda image: image))
    assert twice(5) == 5


def _cgroup(tmp_path, v2: bool, leaf: dict, parent: dict):
    proc = tmp_path / "cgroup"
    root = tmp_path / "fs"
    if v2:
        proc.write_text("0::/job_7/step_0\n")
        base = root
        names = ("memory.max", "memory.current")
    else:
        proc.write_text("12:cpu,cpuacct:/job_7/step_0\n4:memory:/job_7/step_0\n")
        base = root / "memory"
        names = ("memory.limit_in_bytes", "memory.usage_in_bytes")
    for folder, values in ((base / "job_7" / "step_0", leaf), (base / "job_7", parent)):
        folder.mkdir(parents=True, exist_ok=True)
        for name, value in zip(names, values):
            (folder / name).write_text(f"{value}\n")
    return str(proc), str(root)


@pytest.mark.parametrize("v2", [True, False])
def test_the_job_s_cgroup_limit_caps_the_memory_available(tmp_path, v2):
    # The step allows 8 GiB with 2 used; the job above it 6 GiB with 5 used: 1 GiB is what can still be had.
    proc, root = _cgroup(tmp_path, v2, leaf=(8 << 30, 2 << 30), parent=(6 << 30, 5 << 30))
    assert par.cgroup_headroom(proc, root) == 1 << 30
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 400000000 kB\n")                # the node's 381 GiB, which a job cannot have
    assert par.memory_available(str(meminfo), proc_cgroup=proc, cgroup_root=root, environ={}) == 1 << 30


def test_an_unlimited_cgroup_sets_no_cap(tmp_path):
    proc, root = _cgroup(tmp_path, True, leaf=("max", 2 << 30), parent=("max", 5 << 30))
    assert par.cgroup_headroom(proc, root) is None
    assert par.cgroup_headroom(str(tmp_path / "none"), root) is None


def test_slurm_s_allocation_caps_the_memory_available():
    assert par.slurm_allocation({"SLURM_MEM_PER_NODE": "65536"}) == 64 << 30
    assert par.slurm_allocation({"SLURM_MEM_PER_CPU": "4096", "SLURM_CPUS_ON_NODE": "16"}) == 64 << 30
    assert par.slurm_allocation({}) is None


def test_a_gpu_is_in_use_only_when_the_run_said_so(monkeypatch):
    from pipeline.parallelism import GPU_SHARERS_ENV

    assert par.gpu_in_use({GPU_SHARERS_ENV: "2"}) and not par.gpu_in_use({})


def test_memory_is_read_from_the_kernel_and_shared_among_the_workers(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       65000000 kB\nMemFree:  100 kB\nMemAvailable:   50000000 kB\n")
    alone = {"proc_cgroup": str(tmp_path / "no-cgroup"), "environ": {}}
    assert par.memory_available(str(meminfo), **alone) == 50000000 * 1024
    assert par.memory_available(str(tmp_path / "missing"), **alone) is None
    assert par.worker_memory_budget(10 * 2 ** 30, workers=2) == int(10 * 2 ** 30 * par.MEMORY_SHARE / 2)
    assert par.pool_workers({par.POOL_WORKERS_ENV: "6"}) == 6 and par.pool_workers({}) == 1


def _record(tiles_per_tp: int, size=(2048, 2048), timepoints=("0h", "24h")):
    record = {"timepoints": {tp: {"tile_size_px": {"x": size[0], "y": size[1]}} for tp in timepoints},
              "tiles": [{"well": "A01", "timepoint": tp, "field": f"F{k}"} for tp in timepoints
                        for k in range(tiles_per_tp)]}
    return record


def test_timepoints_located_at_once_are_limited_by_the_threads_share_of_memory(monkeypatch):
    from pipeline.nanowell_tiles import CACHE_SHARE

    # 30 tiles of 2048 x 2048, four float32 copies each, and the montage's fixed cost: a timepoint being located.
    per_tp = 30 * 2048 * 2048 * 4 * nr.LOCATE_TILE_COPIES + nr.MONTAGE_WORK_BYTES
    # The budget is shared with the tile cache: the threads plan on (1 - CACHE_SHARE) of it.
    monkeypatch.setattr(par, "worker_memory_budget", lambda: int(2.5 * per_tp / (1 - CACHE_SHARE)))
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 2
    monkeypatch.setattr(par, "worker_memory_budget", lambda: int(2.5 * per_tp))      # all of it would allow 2; half, 1
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 1
    monkeypatch.setattr(par, "worker_memory_budget", lambda: per_tp // 3)
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 1     # never none
    monkeypatch.setattr(par, "worker_memory_budget", lambda: None)                   # not Linux: not limited
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 7


def test_a_small_well_s_estimate_carries_the_montage_s_fixed_cost(monkeypatch):
    """Measured: locating a 2 x 2 well of 2048-pixel tiles held 803-850 MiB above its inputs, mostly the montage's
    float64 arrays, where four copies of four tiles estimated 256 MiB."""
    from pipeline.nanowell_tiles import CACHE_SHARE

    four_tiles = 4 * 2048 * 2048 * 4 * nr.LOCATE_TILE_COPIES
    monkeypatch.setattr(par, "worker_memory_budget", lambda: int(4 * four_tiles / (1 - CACHE_SHARE)))
    assert nr._locate_threads(_record(4), "A01", ["0h", "24h"], threads=7) == 1      # not 4: the montage counts


def _prepared(cellpose: bool, tiles: int = 4, px: int = 2048):
    import types

    import numpy as np

    return types.SimpleNamespace(cellpose=cellpose,
                                 images={f"F{k}": np.zeros((px, px), np.float32) for k in range(tiles)})


def test_cellpose_on_a_cpu_measures_one_tile_at_a_time(monkeypatch):
    monkeypatch.setattr(par, "_usable_cpus", lambda: 64)
    monkeypatch.setenv(par.THREADS_ENV, "6")
    monkeypatch.setattr(par, "worker_memory_budget", lambda: 1 << 40)
    monkeypatch.setattr(par, "gpu_in_use", lambda environ=None: False)
    assert nr._tile_threads(_prepared(cellpose=True)) == 1
    assert nr._tile_threads(_prepared(cellpose=False)) == 6           # a segmenter handed in (Test detection's, a test's)
    monkeypatch.setattr(par, "gpu_in_use", lambda environ=None: True)
    assert nr._tile_threads(_prepared(cellpose=True)) == 6


def test_tiles_measured_at_once_are_limited_by_memory(monkeypatch):
    from pipeline.nanowell_tiles import CACHE_SHARE

    monkeypatch.setattr(par, "_usable_cpus", lambda: 64)
    monkeypatch.setenv(par.THREADS_ENV, "6")
    monkeypatch.setattr(par, "gpu_in_use", lambda environ=None: True)
    prep = _prepared(cellpose=True)
    held = 2 * 4 * 2048 * 2048 * 4                                     # this timepoint's tiles and the next's
    per_tile = 2048 * 2048 * nr.TILE_WORK_BYTES_PER_PX
    monkeypatch.setattr(par, "worker_memory_budget", lambda: int((held + 3 * per_tile) / (1 - CACHE_SHARE)) + 1)
    assert nr._tile_threads(prep) == 3


def test_a_record_without_tile_sizes_is_not_limited(monkeypatch):
    monkeypatch.setattr(par, "worker_memory_budget", lambda: 1)
    record = _record(30)
    for meta in record["timepoints"].values():
        meta.pop("tile_size_px")
    assert nr._locate_threads(record, "A01", ["0h", "24h"], threads=5) == 5


def test_each_limit_names_itself(monkeypatch):
    """The run log says what decided each well's thread counts, so a run that gained less than expected says why."""
    from pipeline.nanowell_tiles import CACHE_SHARE

    monkeypatch.setattr(par, "_usable_cpus", lambda: 64)
    per_tp = 30 * 2048 * 2048 * 4 * nr.LOCATE_TILE_COPIES + nr.MONTAGE_WORK_BYTES
    monkeypatch.setattr(par, "worker_memory_budget", lambda: int(2.5 * per_tp / (1 - CACHE_SHARE)))
    n, why = nr._locate_threads_why(_record(30), "A01", ["0h", "24h"], threads=7)
    assert n == 2 and why.startswith("memory: ") and "holds 2" in why
    monkeypatch.setattr(par, "worker_memory_budget", lambda: 1 << 50)
    assert nr._locate_threads_why(_record(30), "A01", ["0h"], threads=7) == (7, "the worker's 7 threads")
    assert nr._locate_threads_why(_record(30), "A01", ["0h"], threads=1) == (1, "the worker has one thread")
    monkeypatch.setenv(par.THREADS_ENV, "6")
    monkeypatch.setattr(par, "gpu_in_use", lambda environ=None: False)
    assert nr._tile_threads_why(_prepared(cellpose=True)) == (1, "Cellpose has no GPU here and uses every core itself")
    monkeypatch.setattr(par, "gpu_in_use", lambda environ=None: True)
    assert nr._tile_threads_why(_prepared(cellpose=True)) == (6, "the worker's 6 threads")
    monkeypatch.setattr(par, "worker_memory_budget", lambda: 1)
    n, why = nr._tile_threads_why(_prepared(cellpose=True))
    assert n == 1 and why.startswith("memory: ")
