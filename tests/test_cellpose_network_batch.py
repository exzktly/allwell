"""How many network tiles a Cellpose worker runs per pass: measured against its share of the GPU, halved on OOM.

Asked for: the batch size should follow the GPU it runs on and the workers sharing it, rather than a fixed 32 that
fits an A10G in bfloat16 and may not fit a 16 GB T4 in float32 with two workers on it. A worker measures what one
tile costs in GPU memory, once, and sizes its batch to its share; a pass that still runs out of memory is retried at
half the batch, which the worker keeps. The fakes stand in for Torch's memory counters, so none of this needs a GPU.
"""

from __future__ import annotations

import ast
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from pipeline import cellpose as cp
from pipeline import parallelism as par
from tests._repo_scan import parse_source

GiB = 2 ** 30
MiB = 2 ** 20


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_an_a10g_in_bfloat16_with_two_workers_runs_the_ceiling():
    # 24 GiB x 0.8 / 2 = 9.6 GiB, less 0.6 GiB of weights: 9 GiB, 92 tiles of 100 MiB — capped at 32.
    assert cp.batch_for_memory(24 * GiB, 0.6 * GiB, 100 * MiB, 2) == 32


def test_a_t4_in_float32_with_two_workers_runs_fewer():
    # 16 GiB x 0.8 / 2 = 6.4 GiB, less 1.2 GiB: 5.2 GiB over 200 MiB a tile is 26.
    assert cp.batch_for_memory(16 * GiB, 1.2 * GiB, 200 * MiB, 2) == 26


def test_more_workers_on_one_gpu_each_get_a_smaller_batch():
    # Eight workers: 1.6 GiB each, less 1.2 GiB: 0.4 GiB is 2 tiles.
    assert cp.batch_for_memory(16 * GiB, 1.2 * GiB, 200 * MiB, 8) == 2
    # No room left after the weights still runs, one tile a pass; the OOM halving is the backstop.
    assert cp.batch_for_memory(8 * GiB, 4 * GiB, 200 * MiB, 8) == 1


def test_memory_something_else_holds_lowers_the_batch():
    # The 2-worker T4 above, with only 6 GiB free: 6 x 0.8 / 2 = 2.4 GiB over 200 MiB is 12, not the total's 26.
    assert cp.batch_for_memory(16 * GiB, 1.2 * GiB, 200 * MiB, 2, free_bytes=6 * GiB) == 12


def test_free_memory_never_raises_the_batch_above_the_total_s_share():
    # All 16 GiB free: 16 x 0.8 / 2 = 6.4 GiB, but the total's share less the weights is 5.2 GiB, so 26 stands.
    assert cp.batch_for_memory(16 * GiB, 1.2 * GiB, 200 * MiB, 2, free_bytes=16 * GiB) == 26


def test_a_tile_measured_as_free_leaves_the_ceiling():
    assert cp.batch_for_memory(16 * GiB, 1 * GiB, 0, 2) == cp.NETWORK_BATCH


# ── the measurement ──────────────────────────────────────────────────────────

class _Net:
    def __init__(self, weight_bytes: int):
        self._params = [types.SimpleNamespace(numel=lambda: weight_bytes // 2, element_size=lambda: 2)]

    def parameters(self):
        return iter(self._params)


class _Model:
    """A Cellpose 4 model whose passes fail with *oom* above *fits* tiles a pass, and record each batch size."""

    def __init__(self, device, *, fits: int = 10 ** 6, oom=None, memory=None):
        self.device = device
        self.net = _Net(int(1.2 * GiB))                  # Cellpose-SAM in float32, as on a T4
        self.batches: list[int] = []
        self.fits, self.oom, self.memory = fits, oom, memory

    def eval(self, x, batch_size=8, **kw):
        self.batches.append(batch_size)
        if self.memory is not None:                       # a pass's peak: the weights, plus what its tiles take
            self.memory["peak"] = self.memory["allocated"] + batch_size * self.memory["tile"]
        if batch_size > self.fits:
            raise self.oom("CUDA out of memory. Tried to allocate 2.00 GiB")
        return np.zeros(0), [None, np.zeros((2, 1, 1)), np.zeros((1, 1))], None


def _fake_torch(monkeypatch, *, total=16 * GiB, tile=200 * MiB, held_elsewhere=0):
    """*held_elsewhere* is memory other processes hold on the GPU, which only the driver's free count sees."""
    memory = {"allocated": int(0.6 * GiB), "peak": int(0.6 * GiB), "tile": tile, "emptied": 0}
    oom = type("OutOfMemoryError", (RuntimeError,), {})
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(
        OutOfMemoryError=oom,
        get_device_properties=lambda i: types.SimpleNamespace(total_memory=total),
        current_device=lambda: 0,
        synchronize=lambda i=None: None,
        memory_allocated=lambda i=None: memory["allocated"],
        reset_peak_memory_stats=lambda i=None: memory.update(peak=memory["allocated"]),
        max_memory_allocated=lambda i=None: memory["peak"],
        empty_cache=lambda: memory.update(emptied=memory["emptied"] + 1),
        mem_get_info=lambda i=None: (total - memory["allocated"] - held_elsewhere, total),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(cp, "_NETWORK_BATCHES", {})
    return memory, oom


def _cuda(index=0):
    return types.SimpleNamespace(type="cuda", index=index)


def test_a_worker_on_a_gpu_measures_a_tile_once_and_sizes_its_batch_to_its_share(monkeypatch, caplog):
    memory, oom = _fake_torch(monkeypatch, total=16 * GiB, tile=200 * MiB)
    monkeypatch.setenv(par.GPU_SHARERS_ENV, "2")
    model = _Model(_cuda(), oom=oom, memory=memory)
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        assert cp._network_batch(model, "cpsam") == 26
        assert cp._network_batch(model, "cpsam") == 26
    assert model.batches == [cp.PROBE_TILES]                       # measured once, by one pass of blank tiles
    line = next(r.getMessage() for r in caplog.records if "network batch" in r.getMessage())
    assert "26 tiles a pass" in line and "a tile takes 200 MB" in line
    assert "2 worker(s) share cuda:0's 16.0 GB, 15.4 GB of it free" in line


def test_a_gpu_another_process_fills_gives_the_worker_the_batch_that_fits_what_is_free(monkeypatch, caplog):
    """Reported: four L4s, ten workers, and the run ran out of GPU memory. The batch was sized from each GPU's total,
    so memory another process held on it was planned on as if it were this run's."""
    memory, oom = _fake_torch(monkeypatch, total=24 * GiB, tile=200 * MiB, held_elsewhere=16 * GiB)
    monkeypatch.setenv(par.GPU_SHARERS_ENV, "3")
    model = _Model(_cuda(), oom=oom, memory=memory)
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        # Free: 24 - 0.6 - 16 = 7.4 GiB; x 0.8 / 3 = 1.97 GiB over 200 MiB is 10. The total alone gave 26.
        assert cp._network_batch(model, "cpsam") == 10
    line = next(r.getMessage() for r in caplog.records if "network batch" in r.getMessage())
    assert "3 worker(s) share cuda:0's 24.0 GB, 7.4 GB of it free" in line
    assert memory["emptied"] == 1                        # the measuring pass's cache handed back before reading free


def test_a_driver_that_cannot_say_what_is_free_leaves_the_total_s_share(monkeypatch, caplog):
    memory, oom = _fake_torch(monkeypatch, total=16 * GiB, tile=200 * MiB)
    def no_info(i=None):
        raise RuntimeError("CUDA driver error: not supported")
    sys.modules["torch"].cuda.mem_get_info = no_info
    monkeypatch.setenv(par.GPU_SHARERS_ENV, "2")
    with caplog.at_level(logging.INFO):
        assert cp._network_batch(_Model(_cuda(), oom=oom, memory=memory), "cpsam") == 26
    assert any("could not read the free memory on cuda:0" in r.getMessage() for r in caplog.records)
    assert any("free not known" in r.getMessage() for r in caplog.records)


def test_the_same_gpu_shared_by_more_workers_gives_each_a_smaller_batch(monkeypatch):
    memory, oom = _fake_torch(monkeypatch, total=16 * GiB, tile=200 * MiB)
    monkeypatch.setenv(par.GPU_SHARERS_ENV, "8")
    assert cp._network_batch(_Model(_cuda(), oom=oom, memory=memory), "cpsam") == 2


def test_a_worker_not_told_how_many_share_the_gpu_takes_it_as_its_own(monkeypatch):
    memory, oom = _fake_torch(monkeypatch, total=16 * GiB, tile=200 * MiB)
    monkeypatch.delenv(par.GPU_SHARERS_ENV, raising=False)
    # 12.8 GiB less 1.2 is 59 tiles: capped.
    assert cp._network_batch(_Model(_cuda(), oom=oom, memory=memory), "cpsam") == cp.NETWORK_BATCH


def test_a_measuring_pass_that_runs_out_of_memory_starts_the_worker_at_half_of_it(monkeypatch, caplog):
    """The measuring pass sits outside the halving loop, so running out there failed the well."""
    memory, oom = _fake_torch(monkeypatch, total=16 * GiB, tile=200 * MiB)
    model = _Model(_cuda(), fits=1, oom=oom, memory=memory)
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        cp._eval_halving_on_oom(model, "cpsam", np.zeros((3, 4, 4, 1)))
    assert model.batches == [cp.PROBE_TILES, 2, 1]                # measured, then 2, then halved to what fits
    assert cp._NETWORK_BATCHES["cpsam"] == 1 and memory["emptied"] == 2   # after the measuring pass, then the halving
    warned = [r.getMessage() for r in caplog.records]
    assert warned[0].startswith(f"Cellpose cpsam ran out of GPU memory measuring a pass of {cp.PROBE_TILES} tiles; "
                                "running 2 a pass")


def test_off_a_gpu_the_batch_is_the_ceiling_and_nothing_is_measured(monkeypatch):
    monkeypatch.setattr(cp, "_NETWORK_BATCHES", {})
    model = _Model("cpu")
    assert cp._network_batch(model, "cpsam") == cp.NETWORK_BATCH and model.batches == []


# ── halving on out-of-memory ─────────────────────────────────────────────────

def test_a_pass_that_runs_out_of_memory_is_run_again_at_half_and_the_worker_keeps_it(monkeypatch, caplog):
    memory, oom = _fake_torch(monkeypatch)
    model = _Model("cpu", fits=8, oom=oom)
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        cp._eval_halving_on_oom(model, "cpsam", np.zeros((3, 4, 4, 1)))
        cp._eval_halving_on_oom(model, "cpsam", np.zeros((3, 4, 4, 1)))
    assert model.batches == [32, 16, 8, 8]                # the second call starts where the first ended
    assert cp._NETWORK_BATCHES["cpsam"] == 8 and memory["emptied"] == 2
    warned = [r.getMessage() for r in caplog.records if "ran out of GPU memory" in r.getMessage()]
    assert len(warned) == 2 and "at 32 tiles a pass; using 16" in warned[0]


def test_older_torch_s_plain_runtime_error_counts_as_out_of_memory(monkeypatch):
    _fake_torch(monkeypatch)
    model = _Model("cpu", fits=16, oom=RuntimeError)
    cp._eval_halving_on_oom(model, "cpsam", np.zeros((1, 4, 4, 1)))
    assert model.batches == [32, 16]


def test_any_other_failure_is_not_retried(monkeypatch):
    _fake_torch(monkeypatch)
    model = _Model("cpu", fits=0, oom=lambda message: RuntimeError("expected input to have 3 channels"))
    with pytest.raises(RuntimeError, match="3 channels"):
        cp._eval_halving_on_oom(model, "cpsam", np.zeros((1, 4, 4, 1)))
    assert model.batches == [32]


def test_a_single_tile_that_does_not_fit_still_fails(monkeypatch):
    _memory, oom = _fake_torch(monkeypatch)
    model = _Model("cpu", fits=0, oom=oom)
    with pytest.raises(oom):
        cp._eval_halving_on_oom(model, "cpsam", np.zeros((1, 4, 4, 1)))
    assert model.batches == [32, 16, 8, 4, 2, 1]


# ── the run tells its workers how many share a GPU ───────────────────────────

def _args(**over):
    base = dict(tf_threads=0, workers=0, cpu_only=False, segmentation_method="cellpose_labelfree")
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize("n_gpu, workers, method, sharers", [
    (1, 0, "cellpose_labelfree", 2),      # capped at two on one GPU
    (2, 0, "cellpose_labelfree", 2),      # four spread over two
    (2, 3, "cellpose_labelfree", 2),      # three over two: the busier GPU has two
    (1, 6, "cellpose", 6),                # --workers wins over the cap, and all six share the one GPU
    (0, 0, "cellpose_labelfree", 0),      # no GPU
    (1, 0, "stardist_nuclei", 0),         # not Cellpose
])
def test_the_plan_counts_the_workers_on_each_gpu(monkeypatch, n_gpu, workers, method, sharers):
    monkeypatch.setattr(par, "_cpu_count", lambda: 17)
    monkeypatch.setattr(par.sys, "platform", "linux")
    monkeypatch.setattr(par, "visible_gpu_count", lambda: n_gpu)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    plan = par.plan_parallelism(_args(workers=workers, segmentation_method=method), logging.getLogger("test"))
    assert plan.gpu_sharers == sharers


def test_what_the_run_exports_is_what_a_worker_reads(monkeypatch):
    from pipeline import nanowell_parallel as npar

    plan = types.SimpleNamespace(gpu_sharers=3, workers=6)
    monkeypatch.delenv(par.GPU_SHARERS_ENV, raising=False)
    monkeypatch.delenv(npar.POOL_WORKERS_ENV, raising=False)
    par.export_gpu_sharers(plan)
    assert cp._gpu_sharers() == 3 and npar.pool_workers() == 6
    par.export_gpu_sharers(types.SimpleNamespace(gpu_sharers=0, workers=2))   # a later run without a GPU clears it
    assert cp._gpu_sharers() == 1 and npar.pool_workers() == 2


def test_the_run_exports_the_plan_before_it_builds_a_worker_pool():
    """The export has to happen in main(), after the plan and before any pool spawns: a worker reads the variable
    once, when it loads its model."""
    tree = parse_source(Path(__file__).resolve().parents[1] / "process_microscopy.py")
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [(n.lineno, n.func.id) for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    line = {name: no for no, name in sorted(calls, reverse=True)}
    assert line["plan_parallelism"] < line["export_gpu_sharers"]
    pools = [no for no, name in calls if name in ("process_well_zips", "process_well_folders", "_well_pool")]
    assert pools and all(line["export_gpu_sharers"] < no for no in pools)
