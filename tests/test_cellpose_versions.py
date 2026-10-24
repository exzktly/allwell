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


def _install(monkeypatch, major: int, *, cuda: bool = True, bf16: bool = True, torch_version: str = "2.0.1",
             bf16_fails: bool = False) -> dict:
    """A fake Cellpose of *major* and a fake Torch; returns what the model was built with and evaluated with."""
    calls: dict = {"built": None, "builds": [], "eval": [], "images": []}

    class _Model:
        def __init__(self, **kw):
            calls["built"] = kw
            calls["builds"].append(kw)
            self.half = bool(kw.get("use_bfloat16"))

        def eval(self, x, **kw):
            if bf16_fails and self.half:
                raise RuntimeError('"upsample_linear1d_out_frame" not implemented for \'BFloat16\'')
            calls["eval"].append((np.asarray(x).shape, kw))
            calls["images"].append(np.array(x))
            return np.ones(np.asarray(x).shape[:2], dtype=int), None, None

    models = types.SimpleNamespace(CellposeModel=_Model)
    package = types.ModuleType("cellpose")
    package.models = models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cellpose", package)
    monkeypatch.setitem(sys.modules, "cellpose.models", models)
    torch = types.ModuleType("torch")
    torch.__version__ = torch_version
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
    # The pinned Torch (2.0.1) runs Cellpose-SAM in float32 even where the GPU offers bfloat16 (BF16_MIN_TORCH).
    assert built["gpu"] is True and built["use_bfloat16"] is False
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
def test_a_fixed_range_scales_the_image_here_and_cellpose_normalises_nothing(monkeypatch, major):
    """A nanowell's crop is scaled as its whole tile is. Handed to Cellpose as ``normalize={"lowhigh": …}``, the range
    was applied to the empty channels Cellpose pads a greyscale image with, which then read about -1 instead of 0
    (measured on Cellpose 4.1.1's ``convert_image`` / ``normalize_img``) and Cellpose-SAM found no cells. So the image
    is scaled before ``eval`` and ``eval`` is told not to normalise. Without a range, Cellpose scales each image by its
    own percentiles, as before."""
    calls = _install(monkeypatch, major)
    model = "cpsam" if major == 4 else "cyto3"
    image = np.array([[10.0, 130.0], [250.0, 490.0]])
    cp._segment_cellpose(image, model_type=model, lowhigh=(10.0, 250.0))
    cp._segment_cellpose(image, model_type=model)
    (_s1, with_range), (_s2, without) = calls["eval"]
    assert with_range["normalize"] is False and "normalize" not in without
    assert np.allclose(calls["images"][0], [[0.0, 0.5], [1.0, 2.0]]) and np.array_equal(calls["images"][1], image)
    with pytest.raises(ValueError, match="nucleus"):
        cp._segment_cellpose(image, model_type=model, lowhigh=(10.0, 250.0), nucleus_image=image)


def test_loading_a_model_quiets_cellpose_s_bare_no_cell_line(monkeypatch):
    """Reported: "No cell pixels found, it just sits there" — Cellpose's INFO line for every image without a cell,
    one per empty nanowell, naming nothing. The count's own progress lines replace it; Cellpose's warnings still pass."""
    import logging

    loggers = [logging.getLogger(f"cellpose.{n}") for n in ("dynamics", "models", "core")]   # + a batch's tqdm
    old = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.INFO)                           # as a run that logs at INFO leaves it
    try:
        _install(monkeypatch, 4)
        cp._segment_cellpose(np.zeros((4, 4)), model_type="cpsam")
        for lg in loggers[::2]:                             # dynamics, core: INFO is not even made
            assert not lg.isEnabledFor(logging.INFO) and lg.isEnabledFor(logging.WARNING), lg.name

        def passes(name, msg, level=logging.WARNING):
            return logging.getLogger(name).filter(logging.LogRecord(name, level, "", 0, msg, None, None))
        # models' INFO is made, so its two timing lines are counted, and no INFO line of it is printed.
        monkeypatch.setattr(cp, "CELLPOSE_SECONDS", {"network": 1.0, "masks": 0.0})
        assert not passes("cellpose.models", "network run in 2.50s", logging.INFO)
        assert not passes("cellpose.models", "masks created in 0.45s", logging.INFO)
        assert not passes("cellpose.models", ">>>> loading model cpsam", logging.INFO)
        assert cp.CELLPOSE_SECONDS == pytest.approx({"network": 3.5, "masks": 0.45})
        # The two warnings a batch of crops makes as a matter of course are dropped; any other still passes.
        assert not passes("cellpose.models", "3D stack used, but stitch_threshold=0 and do_3D=False, so masks are "
                          "made per plane only")
        assert not passes("cellpose.dynamics", "no seeds found in get_masks_torch - no masks found.")
        assert passes("cellpose.models", "Found more than 3 channels, only using first 3")
    finally:
        for lg, level in zip(loggers, old):
            lg.setLevel(level)
            lg.filters[:] = [f for f in lg.filters if not isinstance(f, cp._CellposeChatter)]


class _BatchModel:
    """Labels each image of what it is given by where it is above 0.5, numbered by the image's place in the call."""

    def __init__(self):
        self.calls = []

    def eval(self, x, **kw):
        x = np.asarray(x)
        self.calls.append((x.copy(), kw))
        planes = x[..., 0] if x.ndim == 4 else x[np.newaxis]
        masks = np.stack([(p > 0.5) * (i + 1) for i, p in enumerate(planes)])
        return (masks if x.ndim == 4 else masks[0]), None, None


@pytest.mark.parametrize("major", [3, 4])
def test_several_crops_go_to_cellpose_4_in_one_call_and_each_comes_back_as_its_own(monkeypatch, major):
    """Reported: Cellpose-SAM took 2.4 s a nanowell on a Tesla T4, one call each. Cellpose 4 takes a batch of
    images and runs their network tiles together: one call, a (N, H, W, 1) stack scaled to the range and padded with
    0, ``normalize=False``; each crop's labels come back cut to its own shape. Cellpose 3 has no batch: one call each."""
    _install(monkeypatch, major)
    model = _BatchModel()
    monkeypatch.setattr(cp, "_cellpose_model", lambda model_type=None: model)
    crops = [np.full((3, 4), 250.0), np.full((2, 5), 10.0), np.array([[250.0, 10.0]])]
    got = cp._segment_cellpose_many(crops, lowhigh=(10.0, 250.0), model_type="cpsam" if major == 4 else "cyto3",
                                    diameter=12.0)
    assert [g.shape for g in got] == [(3, 4), (2, 5), (1, 2)]
    if major == 4:
        (stack, kw), = model.calls
        assert stack.shape == (3, 3, 5, 1) and stack.dtype == np.float32 and kw["normalize"] is False
        assert kw["diameter"] == 12.0
        # Scaled to the range, centred in the common shape, padded with 0.
        assert np.allclose(stack[0, :3, :4, 0], 1.0) and np.allclose(stack[0, :, 4, 0], 0.0)
        assert np.allclose(stack[2, 1, 1:3, 0], [1.0, 0.0]) and np.allclose(stack[2, [0, 2], :, 0], 0.0)
        assert np.array_equal(got[0], np.ones((3, 4))) and not got[1].any() and got[2].tolist() == [[3, 0]]
    else:
        assert len(model.calls) == 3 and all(kw["normalize"] is False for _x, kw in model.calls)
        assert np.array_equal(got[0], np.ones((3, 4))) and not got[1].any() and got[2].tolist() == [[1, 0]]


def test_torch_2_0_never_builds_cellpose_sam_in_bfloat16_even_on_a_gpu_that_offers_it(monkeypatch):
    """Reported: on an A10G the run failed on every image with "upsample_linear1d_out_frame" not implemented for
    'BFloat16'. The pinned Torch 2.0.1 has no bfloat16 kernel for an upsampling Segment Anything's encoder uses, and
    the A10G (unlike the T4) offers bfloat16, so the model was built in it."""
    calls = _install(monkeypatch, 4, torch_version="2.0.1+cu117", bf16=True)
    cp._segment_cellpose(np.zeros((4, 4)), model_type="cpsam")
    assert [b["use_bfloat16"] for b in calls["builds"]] == [False]


def test_a_model_that_cannot_run_in_bfloat16_is_rebuilt_in_float32_before_the_first_image(monkeypatch, caplog):
    """The version gate covers the failure known; the probe covers the next one, at load, not mid-run."""
    import logging

    calls = _install(monkeypatch, 4, torch_version="2.1.0", bf16=True, bf16_fails=True)
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        labels = cp._segment_cellpose(np.zeros((8, 6)), model_type="cpsam")
    assert labels.shape == (8, 6)
    assert [b["use_bfloat16"] for b in calls["builds"]] == [True, False]
    assert any("cannot run in bfloat16" in r.getMessage() for r in caplog.records)
    shapes = [shape for shape, _kw in calls["eval"]]
    assert shapes == [(8, 6)]                       # the probe failed before eval was recorded; the image ran once


def test_a_gpu_and_torch_that_run_bfloat16_keep_it(monkeypatch):
    calls = _install(monkeypatch, 4, torch_version="2.1.0", bf16=True)
    cp._segment_cellpose(np.zeros((4, 4)), model_type="cpsam")
    assert [b["use_bfloat16"] for b in calls["builds"]] == [True]
    assert cp._torch_at_least("2.0.1+cu117", (2, 1)) is False and cp._torch_at_least("2.1.0", (2, 1)) is True
    assert cp._torch_at_least("nightly", (2, 1)) is False
