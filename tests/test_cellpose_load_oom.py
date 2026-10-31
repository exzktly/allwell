"""A Cellpose worker whose model load runs out of GPU memory keeps segmenting instead of failing every well.

Reported: a remote run's log showed ``CellposeUnavailable`` from CUDA running out of memory, and the run then went on
as if it had one worker fewer. The out-of-memory halving (``_eval_halving_on_oom``) only covers passes of a model
that has loaded; running out while the model loads — building it, or the small test pass that checks bfloat16 —
raised ``CellposeUnavailable``, cached nothing, and the worker failed the same way on every well it took next. The
load is now tried again, and after that the worker loads on the CPU. None of this needs a GPU: a stand-in ``torch``
and ``cellpose`` decide when memory runs out.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import sys
import types

import numpy as np
import pytest

import pipeline.cellpose as cp
from pipeline.nanowell_parallel import ON_GPU_ATTR


class _OutOfMemoryError(RuntimeError):
    """Named as Torch names its own, which is how ``_is_out_of_memory`` recognises it."""


_OutOfMemoryError.__name__ = "OutOfMemoryError"


def _fake_torch(memory: dict):
    t = types.ModuleType("torch")
    t.__version__ = "2.3.0"
    t.device = lambda spec: types.SimpleNamespace(type=spec.split(":")[0], spec=spec, index=None)
    t.cuda = types.SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 1, get_device_name=lambda i: "NVIDIA T4",
        is_bf16_supported=lambda: True, OutOfMemoryError=_OutOfMemoryError,
        empty_cache=lambda: memory.update(emptied=memory["emptied"] + 1))
    t.version = types.SimpleNamespace(cuda="12.1")
    t.backends = types.SimpleNamespace(cuda=types.SimpleNamespace(matmul=types.SimpleNamespace(allow_tf32=False)),
                                       cudnn=types.SimpleNamespace(allow_tf32=False))
    return t


@pytest.fixture
def gpu(monkeypatch):
    """A one-GPU worker whose loads fail with out-of-memory while ``memory["gpu_full_for"]`` loads remain, at the
    stage ``memory["fails_at"]`` names: building the model, or its first (bfloat16 test) pass."""
    memory = {"emptied": 0, "gpu_full_for": 0, "fails_at": "build", "built": [], "slept": []}
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(memory))
    monkeypatch.setattr(mp.current_process(), "_identity", (1,), raising=False)
    monkeypatch.setattr(cp, "_CELLPOSE_MODELS", {})
    monkeypatch.setattr("time.sleep", lambda s: memory["slept"].append(s))

    def out_of_memory_now(device) -> bool:
        if device.type == "cuda" and memory["gpu_full_for"] > 0:
            memory["gpu_full_for"] -= 1
            return True
        return False

    class _Model:
        def __init__(self, **kw):
            self.device = kw["device"]
            memory["built"].append((self.device.spec, kw.get("use_bfloat16")))
            if memory["fails_at"] == "build" and out_of_memory_now(self.device):
                raise _OutOfMemoryError("CUDA out of memory. Tried to allocate 20.00 MiB")
            self._tested = False

        def eval(self, x, **kw):
            if memory["fails_at"] == "test pass" and not self._tested:
                self._tested = True
                if out_of_memory_now(self.device):
                    raise _OutOfMemoryError("CUDA out of memory. Tried to allocate 64.00 MiB")
            self._tested = True
            return np.zeros(np.asarray(x).shape[:2], dtype=int), None, None

    models = types.SimpleNamespace(CellposeModel=_Model)
    package = types.ModuleType("cellpose")
    package.models = models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cellpose", package)
    monkeypatch.setitem(sys.modules, "cellpose.models", models)
    return memory


def _segment(model_type="cyto3"):
    """What a well's task calls for each field (``process_microscopy`` → ``_segment_cellpose``)."""
    return cp._segment_cellpose(np.zeros((8, 8), np.float32), model_type=model_type)


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_a_load_that_runs_out_of_gpu_memory_is_tried_again_and_stays_on_the_gpu(gpu, caplog):
    gpu["gpu_full_for"] = 2
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        _segment()
    assert gpu["built"] == [("cuda:0", None)] * 3                 # two loads out of memory, the third fits
    assert gpu["slept"] == [cp.LOAD_OOM_WAIT_S] * 2 and gpu["emptied"] == 2
    model = cp._CELLPOSE_MODELS["cyto3"]
    assert model.device.spec == "cuda:0" and getattr(model, ON_GPU_ATTR) is True
    warned = _warnings(caplog)
    assert len(warned) == 2
    assert warned[0].startswith("Cellpose cyto3 ran out of GPU memory loading on GPU cuda:0 (NVIDIA T4) "
                                "(OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB); "
                                "trying again in 10 s, 1 of 3")
    assert any(r.getMessage().startswith("Cellpose cyto3 on GPU cuda:0 (NVIDIA T4), TF32 (worker ")
               for r in caplog.records)


def test_a_gpu_that_never_has_room_leaves_the_worker_segmenting_on_the_cpu(gpu, caplog):
    """The reported case: before, every well this worker took raised CellposeUnavailable."""
    gpu["gpu_full_for"] = 10 ** 6
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        labels = _segment()
        _segment()                                                # the next field: loaded once, not again
    assert labels.shape == (8, 8)
    tries = cp.LOAD_OOM_RETRIES + 1
    assert gpu["built"] == [("cuda:0", None)] * tries + [("cpu", None)]
    assert gpu["slept"] == [cp.LOAD_OOM_WAIT_S] * cp.LOAD_OOM_RETRIES
    model = cp._CELLPOSE_MODELS["cyto3"]
    assert model.device.spec == "cpu"
    assert getattr(model, ON_GPU_ATTR) is False                   # the nanowell count's threads follow it off the GPU
    warned = _warnings(caplog)
    assert len(warned) == tries
    assert warned[-1].startswith(f"Cellpose cyto3 could not load on GPU cuda:0 (NVIDIA T4): it ran out of GPU "
                                 f"memory on all {tries} tries (OutOfMemoryError: CUDA out of memory.")
    assert "runs Cellpose on the CPU for the rest of the run" in warned[-1]
    assert any(r.getMessage().startswith("Cellpose cyto3 on CPU — GPU cuda:0 (NVIDIA T4) had no room for the model "
                                         "(out of GPU memory) (worker ") for r in caplog.records)


def test_running_out_in_the_bfloat16_test_pass_is_tried_again_too(gpu, monkeypatch, caplog):
    """On Cellpose 4 the model's first pass runs inside the load (``_bfloat16_or_float32``), on a GPU already
    holding the weights — the likelier place for a crowded GPU to run out."""
    monkeypatch.setattr(cp, "cellpose_major", lambda version=None: 4)
    gpu["fails_at"], gpu["gpu_full_for"] = "test pass", 1
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        cp._cellpose_model("cpsam")
    assert gpu["built"] == [("cuda:0", True), ("cuda:0", True)]   # bfloat16 both times: not mistaken for no kernel
    assert getattr(cp._CELLPOSE_MODELS["cpsam"], ON_GPU_ATTR) is True
    assert any("Cellpose cpsam on GPU cuda:0 (NVIDIA T4), TF32, bfloat16" in r.getMessage() for r in caplog.records)


def test_a_load_failure_that_is_not_out_of_memory_is_not_tried_again(gpu, monkeypatch):
    class _Broken:
        def __init__(self, **kw):
            raise RuntimeError("weights could not be downloaded")

    monkeypatch.setattr(sys.modules["cellpose.models"], "CellposeModel", _Broken)
    with pytest.raises(cp.CellposeUnavailable, match="weights could not be downloaded"):
        _segment()
    assert gpu["slept"] == [] and gpu["emptied"] == 0
