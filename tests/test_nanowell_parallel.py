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


def test_the_thread_budget_never_exceeds_the_machine(monkeypatch):
    monkeypatch.setattr(par.os, "cpu_count", lambda: 4)
    assert par.thread_budget({par.THREADS_ENV: "64"}) == 4


def test_memory_is_read_from_the_kernel_and_shared_among_the_workers(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       65000000 kB\nMemFree:  100 kB\nMemAvailable:   50000000 kB\n")
    assert par.memory_available(str(meminfo)) == 50000000 * 1024
    assert par.memory_available(str(tmp_path / "missing")) is None
    assert par.worker_memory_budget(10 * 2 ** 30, workers=2) == int(10 * 2 ** 30 * par.MEMORY_SHARE / 2)
    assert par.pool_workers({par.POOL_WORKERS_ENV: "6"}) == 6 and par.pool_workers({}) == 1


def _record(tiles_per_tp: int, size=(2048, 2048), timepoints=("0h", "24h")):
    record = {"timepoints": {tp: {"tile_size_px": {"x": size[0], "y": size[1]}} for tp in timepoints},
              "tiles": [{"well": "A01", "timepoint": tp, "field": f"F{k}"} for tp in timepoints
                        for k in range(tiles_per_tp)]}
    return record


def test_timepoints_located_at_once_are_limited_by_the_worker_s_memory(monkeypatch):
    # 30 tiles of 2048 x 2048, four float32 copies each: 2 GiB a timepoint being located.
    per_tp = 30 * 2048 * 2048 * 4 * nr.LOCATE_TILE_COPIES
    monkeypatch.setattr(par, "worker_memory_budget", lambda: int(2.5 * per_tp))
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 2
    monkeypatch.setattr(par, "worker_memory_budget", lambda: per_tp // 3)
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 1     # never none
    monkeypatch.setattr(par, "worker_memory_budget", lambda: None)                   # not Linux: not limited
    assert nr._locate_threads(_record(30), "A01", ["0h", "24h"], threads=7) == 7


def test_a_record_without_tile_sizes_is_not_limited(monkeypatch):
    monkeypatch.setattr(par, "worker_memory_budget", lambda: 1)
    record = _record(30)
    for meta in record["timepoints"].values():
        meta.pop("tile_size_px")
    assert nr._locate_threads(record, "A01", ["0h", "24h"], threads=5) == 5
