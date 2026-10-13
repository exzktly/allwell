"""Cellpose runs on a GPU when the node it lands on has one.

The model was built as ``CellposeModel(model_type=key)`` — Cellpose's default is
CPU — so a ``--gres=gpu`` node still segmented on CPU, at about 40 s per field
in the run that reported it. Each worker now asks Torch for a CUDA device, and
the main process caps how many workers share the GPUs.

Torch is not installed in the test stack, and the real thing would need a GPU,
so these tests hand the code a stand-in ``torch`` whose answers are the ones a
GPU node, a CPU node and a CPU-only Torch build give. What they pin is the
decision and what reaches ``CellposeModel``, not CUDA itself.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import subprocess
import sys
import types

import numpy as np
import pytest

import pipeline.cellpose as cp
import pipeline.parallelism as par


# ── counting GPUs in the main process, without Torch ─────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("0", 1), ("0,1", 2), ("2, 3 ,", 2), ("", 0), ("-1", 0), ("NoDevFiles", 0),
])
def test_cuda_visible_devices_decides_when_set(monkeypatch, value, expected):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", value)
    monkeypatch.setattr(par.subprocess, "run",
                        lambda *a, **k: pytest.fail("nvidia-smi asked anyway"))
    assert par.visible_gpu_count() == expected


def test_nvidia_smi_is_asked_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    listing = ("GPU 0: NVIDIA A100-SXM4-40GB (UUID: GPU-1)\n"
               "GPU 1: NVIDIA A100-SXM4-40GB (UUID: GPU-2)\n")
    monkeypatch.setattr(par.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, listing, ""))
    assert par.visible_gpu_count() == 2


def test_a_host_without_nvidia_smi_has_no_gpu(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(par.subprocess, "run", missing)
    assert par.visible_gpu_count() == 0


# ── the worker plan ──────────────────────────────────────────────────────────

def _args(**over):
    base = dict(tf_threads=0, workers=0, cpu_only=False,
                segmentation_method="cellpose_labelfree")
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def linux_17_cores(monkeypatch):
    """16 cores for workers after the main process's one: the CPU plan is
    4 workers x 4 threads, more than one GPU's cap of 2."""
    monkeypatch.setattr(par, "_cpu_count", lambda: 17)
    monkeypatch.setattr(par.sys, "platform", "linux")


def _plan(monkeypatch, n_gpu, **over):
    monkeypatch.setattr(par, "visible_gpu_count", lambda: n_gpu)
    return par.plan_parallelism(_args(**over), logging.getLogger("test"))


def test_one_gpu_caps_cellpose_at_two_workers_with_the_cores_shared(
        monkeypatch, linux_17_cores):
    plan = _plan(monkeypatch, 1)
    assert (plan.workers, plan.threads_per_worker) == (2, 8)
    assert "capped at 2" in plan.cellpose_note


def test_two_gpus_allow_four(monkeypatch, linux_17_cores):
    plan = _plan(monkeypatch, 2)
    assert (plan.workers, plan.threads_per_worker) == (4, 4)


def test_no_gpu_leaves_the_cpu_plan_and_says_why(monkeypatch, linux_17_cores):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    plan = _plan(monkeypatch, 0)
    assert (plan.workers, plan.threads_per_worker) == (4, 4)
    assert plan.cellpose_note == "CPU — no GPU visible on this node (CUDA_VISIBLE_DEVICES='')"


def test_workers_override_wins_over_the_gpu_cap(monkeypatch, linux_17_cores):
    plan = _plan(monkeypatch, 1, workers=6)
    assert plan.workers == 6


def test_cpu_only_does_not_keep_cellpose_off_the_gpu(monkeypatch, linux_17_cores):
    """A remote host's cpu_only is written from the *login* node's TensorFlow,
    so it is true on most clusters; honouring it would keep Cellpose off the
    GPU node the launcher asked for. It stays StarDist's switch."""
    plan = _plan(monkeypatch, 1, cpu_only=True)
    assert plan.workers == 2
    assert plan.cellpose_note.startswith("GPU")
    assert plan.device_str == "CPU only (--cpu_only)"


def test_a_stardist_run_is_untouched_by_the_gpu(monkeypatch, linux_17_cores):
    plan = _plan(monkeypatch, 1, segmentation_method="stardist_nuclei")
    assert (plan.workers, plan.threads_per_worker, plan.cellpose_note) == (4, 4, "")


# ── the device each worker's model is put on ─────────────────────────────────

def _fake_torch(*, cuda: bool, count: int = 1, built_for="11.7"):
    t = types.ModuleType("torch")
    t.__version__ = "2.0.1"
    t.device = lambda spec: types.SimpleNamespace(type=spec.split(":")[0], spec=spec)
    t.cuda = types.SimpleNamespace(
        is_available=lambda: cuda, device_count=lambda: count,
        get_device_name=lambda i: f"NVIDIA A100 #{i}")
    t.version = types.SimpleNamespace(cuda=built_for)
    return t


def _ordinal(monkeypatch, n: int) -> None:
    monkeypatch.setattr(mp.current_process(), "_identity", (n,), raising=False)


def test_a_gpu_node_puts_the_model_on_cuda(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=True))
    _ordinal(monkeypatch, 1)
    device, where = cp._cellpose_device()
    assert device.spec == "cuda:0"
    assert where == "GPU cuda:0 (NVIDIA A100 #0)"


def test_workers_are_spread_across_gpus(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=True, count=2))
    seen = []
    for n in (1, 2, 3, 4):
        _ordinal(monkeypatch, n)
        seen.append(cp._cellpose_device()[0].spec)
    assert seen == ["cuda:0", "cuda:1", "cuda:0", "cuda:1"]


def test_a_cpu_only_torch_build_says_so(monkeypatch):
    """The case most likely to surprise: a GPU node whose installed Torch cannot
    use it. The log has to name it rather than just say CPU."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=False, built_for=None))
    device, where = cp._cellpose_device()
    assert device.spec == "cpu"
    assert where == ("CPU — Torch 2.0.1 sees no CUDA device "
                     "(built for CUDA none (CPU-only build); CUDA_VISIBLE_DEVICES=0)")


def test_the_model_is_built_on_that_device_and_the_worker_says_where(
        monkeypatch, caplog):
    """The call that shipped the defect, at the boundary: what CellposeModel
    receives, and the line the run log carries."""
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=True))
    _ordinal(monkeypatch, 1)
    built = {}

    class _Model:
        def __init__(self, **kw):
            built.update(kw)

        def eval(self, x, **kw):
            return np.zeros(np.asarray(x).shape[:2], dtype=int), None, None

    fake_models = types.SimpleNamespace(CellposeModel=_Model)
    fake_cellpose = types.ModuleType("cellpose")
    fake_cellpose.models = fake_models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cellpose", fake_cellpose)
    monkeypatch.setitem(sys.modules, "cellpose.models", fake_models)
    monkeypatch.setattr(cp, "_CELLPOSE_MODELS", {})

    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        cp._segment_cellpose(np.zeros((8, 8), np.float32), model_type="cyto3")

    assert built.get("model_type") == "cyto3", built
    assert built.get("gpu") is True, f"model built without gpu=True: {built}"
    assert getattr(built.get("device"), "spec", None) == "cuda:0", built
    assert any("Cellpose cyto3 on GPU cuda:0 (NVIDIA A100 #0)" in r.getMessage()
               for r in caplog.records)
