"""A Cellpose worker whose model load runs out of GPU memory waits for room instead of failing every well.

Reported: a remote run's log showed ``CellposeUnavailable`` from CUDA running out of memory, and the run then went on
as if it had one worker fewer. The out-of-memory halving (``_eval_halving_on_oom``) only covers passes of a model
that has loaded; running out while the model loads — building it, or the small test pass that checks bfloat16 —
raised ``CellposeUnavailable``, cached nothing, and the worker failed the same way on every well it took next.

The load is now tried again until it fits, with a wait that doubles each time up to a cap, and never falls back to
the CPU. So that the wait can end, a worker whose Cellpose is on a GPU hands back Torch's cached GPU memory when it
finishes a well; without that, an idle neighbour would hold its cache for ever after the last well, and the waiting
worker would wait with it. None of this needs a GPU: a stand-in ``torch`` and ``cellpose`` decide when memory runs
out.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import sys
import types

import numpy as np
import pytest

import pipeline.cellpose as cp
import pipeline.nanowell_parallel as npar
import pipeline.nanowell_task as nt
import process_microscopy as pm
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


# ── the load waits for room on the GPU ───────────────────────────────────────

def test_a_load_that_runs_out_of_gpu_memory_waits_longer_each_time_and_never_leaves_the_gpu(gpu, caplog):
    """Eight failed loads: waits of 10, 20, 40, 80, 160 s, then the 300 s cap three times; every try on the GPU,
    none on the CPU, and the ninth load fits."""
    gpu["gpu_full_for"] = 8
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        labels = _segment()
        _segment()                                                # the next field: loaded once, not again
    assert labels.shape == (8, 8)
    assert gpu["built"] == [("cuda:0", None)] * 9
    assert gpu["slept"] == [10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0, 300.0]
    assert gpu["emptied"] == 8
    model = cp._CELLPOSE_MODELS["cyto3"]
    assert model.device.spec == "cuda:0" and getattr(model, ON_GPU_ATTR) is True
    warned = _warnings(caplog)
    assert len(warned) == 8
    assert warned[0].startswith("Cellpose cyto3 ran out of GPU memory loading on GPU cuda:0 (NVIDIA T4) "
                                "(OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB), most likely "
                                "because the run's other workers hold it; trying again in 10 s (try 1, worker ")
    assert "trying again in 300 s (try 8, worker " in warned[-1]
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("Cellpose cyto3 loaded on GPU cuda:0 (NVIDIA T4) after 8 out-of-memory tries")
               for m in messages)
    assert any(m.startswith("Cellpose cyto3 on GPU cuda:0 (NVIDIA T4), TF32 (worker ") for m in messages)


def test_running_out_in_the_bfloat16_test_pass_is_tried_again_too(gpu, monkeypatch, caplog):
    """On Cellpose 4 the model's first pass runs inside the load (``_bfloat16_or_float32``), on a GPU already
    holding the weights — the likelier place for a crowded GPU to run out."""
    monkeypatch.setattr(cp, "cellpose_major", lambda version=None: 4)
    gpu["fails_at"], gpu["gpu_full_for"] = "test pass", 1
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        cp._cellpose_model("cpsam")
    assert gpu["built"] == [("cuda:0", True), ("cuda:0", True)]   # bfloat16 both times: not mistaken for no kernel
    assert gpu["slept"] == [10.0]
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


# ── a worker that finishes a well makes room for one still waiting ───────────

def test_a_gpu_worker_hands_back_its_cache_at_the_end_of_a_well(gpu):
    _segment()
    before = gpu["emptied"]
    npar.release_cellpose_gpu_cache()
    assert gpu["emptied"] == before + 1


def test_a_cpu_worker_releases_nothing(gpu, monkeypatch):
    monkeypatch.setattr(sys.modules["torch"].cuda, "is_available", lambda: False)
    _segment()
    npar.release_cellpose_gpu_cache()
    assert gpu["emptied"] == 0


def test_a_worker_that_never_ran_cellpose_does_not_import_it_or_torch(monkeypatch):
    """A StarDist worker: TensorFlow and Torch cannot always share a process."""
    monkeypatch.delitem(sys.modules, "pipeline.cellpose")
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    npar.release_cellpose_gpu_cache()
    assert "pipeline.cellpose" not in sys.modules and "torch" not in sys.modules


@pytest.fixture
def released(monkeypatch):
    calls = []
    monkeypatch.setattr(pm, "release_cellpose_gpu_cache", lambda: calls.append(1))
    monkeypatch.setattr(nt, "release_cellpose_gpu_cache", lambda: calls.append(1))
    return calls


def _boom(*a, **k):
    raise RuntimeError("this well failed")


def test_the_zip_well_task_releases_even_when_the_well_fails(released, monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "find_image_groups_in_zip", _boom)
    with pytest.raises(RuntimeError, match="this well failed"):
        pm.process_well_zip_task("A01", tmp_path / "A01.zip", tmp_path / "out", {}, "DAPI", [], "cells")
    assert released == [1]


def test_the_folder_well_task_releases_even_when_the_well_fails(released, monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "find_image_groups", _boom)
    (tmp_path / "A01").mkdir()
    with pytest.raises(RuntimeError, match="this well failed"):
        pm.process_well_folder_task("A01", tmp_path / "A01", tmp_path / "out", {}, "DAPI", [], "cells")
    assert released == [1]


def test_the_reanchoring_pass_releases_after_each_well(released, monkeypatch, tmp_path):
    monkeypatch.setattr(nt, "run_zip_task", lambda *a, **k: None)
    kw = dict(output_dir=tmp_path, shared_kwargs_template={}, nuclear_token="DAPI", fluor_tokens=[],
              csv_prefix="cells", schema=None, sep="_", settings_key="", grouper=None, parse=None, segmenter=None)
    assert nt._reanchor_well(("A01", tmp_path / "A01.zip", {"T0": None}), **kw) == ("A01", {"T0": None}, "")
    monkeypatch.setattr(nt, "run_zip_task", _boom)
    assert nt._reanchor_well(("A02", tmp_path / "A02.zip", {"T0": None}), **kw)[2] == "RuntimeError: this well failed"
    assert released == [1, 1]
