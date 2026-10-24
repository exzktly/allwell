"""Cellpose 3 and Cellpose 4 are both driven correctly, and a model the installed version lacks is refused by name.

Cellpose 4 (the pin, for Cellpose-SAM) replaced every Cellpose 3 model, and its ``CellposeModel`` ignores
``model_type`` and answers an unknown ``pretrained_model`` with its own default — each with only a log line. Built the
way Cellpose 3 is built, a run asking Cellpose 4 for ``cyto3`` would have segmented with Cellpose-SAM and logged
"cyto3". These hold what each version receives, at the boundary: the constructor's and ``eval``'s arguments.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from pipeline import cellpose as cp


def _install(monkeypatch, major: int, *, cuda: bool = True, bf16: bool = True) -> dict:
    """A fake Cellpose of *major* and a fake Torch; returns what the model was built with and evaluated with."""
    calls: dict = {"built": None, "eval": []}

    class _Model:
        def __init__(self, **kw):
            calls["built"] = kw

        def eval(self, x, **kw):
            calls["eval"].append((np.asarray(x).shape, kw))
            return np.ones(np.asarray(x).shape[:2], dtype=int), None, None

    models = types.SimpleNamespace(CellposeModel=_Model)
    package = types.ModuleType("cellpose")
    package.models = models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cellpose", package)
    monkeypatch.setitem(sys.modules, "cellpose.models", models)
    torch = types.ModuleType("torch")
    torch.__version__ = "2.0.1"
    torch.device = lambda spec: types.SimpleNamespace(type=spec.split(":")[0], spec=spec)
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda, device_count=lambda: 1,
                                       get_device_name=lambda i: "GPU", is_bf16_supported=lambda: bf16)
    torch.version = types.SimpleNamespace(cuda="11.7" if cuda else None)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(cp, "cellpose_major", lambda version=None: major)
    monkeypatch.setattr(cp, "_CELLPOSE_MODELS", {})
    return calls


def test_cellpose_4_builds_cellpose_sam_by_name_and_evaluates_without_channels(monkeypatch):
    calls = _install(monkeypatch, 4)
    labels = cp._segment_cellpose(np.zeros((8, 6), np.float32), model_type="cpsam", diameter=12.0)
    assert labels.shape == (8, 6) and labels.dtype == np.int32
    built = calls["built"]
    assert built["pretrained_model"] == "cpsam" and "model_type" not in built
    assert built["gpu"] is True and built["use_bfloat16"] is True
    (shape, kw), = calls["eval"]
    assert shape == (8, 6) and "channels" not in kw and kw["diameter"] == 12.0


def test_cellpose_4_stacks_a_nucleus_channel_on_the_last_axis(monkeypatch):
    calls = _install(monkeypatch, 4)
    cp._segment_cellpose(np.zeros((8, 6)), model_type="cpsam", nucleus_image=np.ones((8, 6)))
    (shape, kw), = calls["eval"]
    assert shape == (8, 6, 2) and kw["channel_axis"] == -1 and "channels" not in kw


@pytest.mark.parametrize("cuda, bf16, expected", [(True, False, False), (False, True, False)])
def test_bfloat16_only_on_a_gpu_that_computes_in_it(monkeypatch, cuda, bf16, expected):
    calls = _install(monkeypatch, 4, cuda=cuda, bf16=bf16)
    cp._segment_cellpose(np.zeros((4, 4)), model_type="cpsam")
    assert calls["built"]["use_bfloat16"] is expected


@pytest.mark.parametrize("model", ["cyto3", "livecell_cp3", "nuclei"])
def test_cellpose_4_refuses_a_cellpose_3_model_by_name_and_builds_nothing(monkeypatch, model):
    calls = _install(monkeypatch, 4)
    with pytest.raises(cp.CellposeUnavailable, match=f"{model} is a Cellpose 3 model.*version 4.*cpsam"):
        cp._segment_cellpose(np.zeros((4, 4)), model_type=model)
    assert calls["built"] is None and calls["eval"] == []


def test_cellpose_3_refuses_cellpose_sam_and_still_drives_its_own_models(monkeypatch):
    calls = _install(monkeypatch, 3)
    with pytest.raises(cp.CellposeUnavailable, match="cpsam needs Cellpose 4"):
        cp._segment_cellpose(np.zeros((4, 4)), model_type="cpsam")
    assert calls["built"] is None
    cp._segment_cellpose(np.zeros((4, 4)), model_type="cyto3")
    assert calls["built"]["model_type"] == "cyto3" and "pretrained_model" not in calls["built"]
    (_shape, kw), = calls["eval"]
    assert kw["channels"] == [0, 0]


def test_the_refusal_is_said_for_each_version_and_silent_where_it_cannot_know():
    assert cp.model_refusal("cpsam", 4) is None and cp.model_refusal("cyto3", 3) is None
    assert cp.model_refusal("cyto3", None) is None                 # not installed: the import says so instead
    assert "not a Cellpose 4 model" in cp.model_refusal("mystery", 4)
    assert cp.cellpose_major("4.1.1") == 4 and cp.cellpose_major("3.1.1.2") == 3


def test_the_default_model_is_the_one_the_pinned_cellpose_ships():
    """The pin and the default move together: a default the pinned version refuses would fail every run."""
    from pathlib import Path

    pin = next(line.split("==")[1].split()[0] for line in
               (Path(__file__).resolve().parents[1] / "requirements-remote.txt").read_text().splitlines()
               if line.startswith("cellpose=="))
    assert cp.model_refusal(cp.DEFAULT_MODEL, cp.cellpose_major(pin)) is None


@pytest.mark.parametrize("major", [3, 4])
def test_a_fixed_range_reaches_cellpose_as_its_normalisation(monkeypatch, major):
    """A nanowell's crop is scaled as its whole tile is: the range goes to ``eval`` as ``normalize={"lowhigh": …}``,
    which both versions read; without one, Cellpose scales each image by its own percentiles, as before."""
    calls = _install(monkeypatch, major)
    model = "cpsam" if major == 4 else "cyto3"
    cp._segment_cellpose(np.zeros((4, 4)), model_type=model, lowhigh=(10.0, 250.0))
    cp._segment_cellpose(np.zeros((4, 4)), model_type=model)
    (_s1, with_range), (_s2, without) = calls["eval"]
    assert with_range["normalize"] == {"lowhigh": [10.0, 250.0]} and "normalize" not in without
