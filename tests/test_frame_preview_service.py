"""The GUI-side StarDist inference behind the Playground's Segment button.

Two properties matter. It must not reimplement anything — segmentation goes
through the pipeline's own ``segment_nuclei``, so what the Playground shows
cannot disagree with what a run produces. And a missing install must be
*reported by name*: a preview that simply failed to draw an overlay would read as
a bug in the segmentation rather than as "TensorFlow isn't here".

Qt-free and TF-free — the model is a stub, so this runs in the fast lane.
"""

from __future__ import annotations

import numpy as np
import pytest

import services.frame_preview as FP


def test_a_missing_install_is_named(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert FP.missing_stardist_deps() == ["stardist", "csbdeep", "tensorflow"]
    with pytest.raises(RuntimeError, match="not installed"):
        FP.segment_frame(np.zeros((8, 8), np.float32))


def test_the_named_failure_lists_what_is_actually_absent(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: None if name == "tensorflow" else real("sys"))
    assert FP.missing_stardist_deps() == ["tensorflow"]


def test_segmentation_calls_the_pipelines_own_inference(monkeypatch):
    """Not a lookalike: it must run ``segment_nuclei``, so the input scrubbing
    and the near-constant-field refusal that make the uncatchable ClipperLib
    abort unreachable apply in the GUI too."""
    import pipeline.stardist as sd

    monkeypatch.setattr(FP, "missing_stardist_deps", lambda: [])
    monkeypatch.setattr(FP, "_GUI_STARDIST_MODEL", object())
    seen = {}

    def _spy(arr, *, model, params):
        seen["params"] = params
        return np.zeros(arr.shape, dtype=np.int32)
    monkeypatch.setattr(sd, "segment_nuclei", _spy)

    FP.segment_frame(np.zeros((8, 8), np.float32),
                     prob_thresh=0.25, nms_thresh=0.5, tile_px=512)
    assert seen["params"] == sd.StarDistParams(prob_thresh=0.25, nms_thresh=0.5,
                                               tile_px=512)


def test_thresholds_default_to_the_models_own(monkeypatch):
    import pipeline.stardist as sd

    monkeypatch.setattr(FP, "missing_stardist_deps", lambda: [])
    monkeypatch.setattr(FP, "_GUI_STARDIST_MODEL", object())
    seen = {}
    monkeypatch.setattr(sd, "segment_nuclei",
                        lambda arr, *, model, params: seen.update(params=params)
                        or np.zeros(arr.shape, dtype=np.int32))
    FP.segment_frame(np.zeros((8, 8), np.float32))
    assert seen["params"] == sd.DEFAULT_STARDIST_PARAMS


def test_the_model_is_loaded_at_most_once_per_session(monkeypatch):
    """A second copy of the weights would be another 1-2 GB for no benefit.

    Needs a real StarDist install: the loader call sits after
    ``from stardist.models import StarDist2D``, so without it the import raises
    before the thing under test runs.
    """
    pytest.importorskip("stardist")
    import pipeline.stardist as sd
    import process_microscopy as pm

    monkeypatch.setattr(FP, "_GUI_STARDIST_MODEL", None)
    loads: list[str] = []
    monkeypatch.setattr(pm, "_ensure_pkg_resources_shim", lambda: None)
    monkeypatch.setattr(pm, "_load_stardist_pretrained",
                        lambda cls, name: loads.append(name) or object())
    monkeypatch.setattr(sd, "segment_nuclei",
                        lambda arr, *, model, params: np.zeros(arr.shape, dtype=np.int32))

    FP.segment_frame(np.zeros((8, 8), np.float32))
    FP.segment_frame(np.zeros((8, 8), np.float32))
    assert loads == ["2D_versatile_fluo"]
