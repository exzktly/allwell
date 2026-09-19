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


# ── the threshold sweep ─────────────────────────────────────────────────────
#
# The fast path was verified against a real StarDist 0.8.5 + TF 2.13 in an
# isolated venv: the shipped ``_resolve_at_thresholds`` returned counts
# identical to nineteen full ``predict_instances`` calls on two fixtures, at
# 5.1x and 10.1x. That cannot run here (CLAUDE.md §10.6 — the session hook
# installs no TF), so what these tests pin is the *plumbing* around it: which
# path is taken, that a stop reaches the loop, and that a build without the
# private API degrades instead of failing.

import numpy as np  # noqa: E402

import services.frame_preview as fp  # noqa: E402


class _FakeModel:
    """A model whose split API works, counting how often each half is used."""

    def __init__(self, counts):
        self._counts = list(counts)
        self.predicts = 0
        self.resolves = 0

    def predict(self, frame):
        self.predicts += 1
        return (np.zeros((4, 4)), np.zeros((4, 4, 32)))

    def _instances_from_prediction(self, shape, prob, dist, *, prob_thresh,
                                   nms_thresh=None):
        n = self._counts[self.resolves]
        self.resolves += 1
        return np.full((3, 3), n, dtype=np.int32), None


def test_the_fast_path_runs_one_inference_for_the_whole_sweep(monkeypatch):
    """Excludes the sweep that calls predict once per threshold.

    That is the whole point of the split API, and it is invisible in the
    counts — both shapes return the same numbers, which is exactly why it was
    measured rather than assumed.
    """
    model = _FakeModel([9, 8, 7, 6])
    counts = fp._resolve_at_thresholds(model, np.zeros((8, 8)), [0.1, 0.2, 0.3, 0.4],
                                       None, None, None)
    assert counts == [9, 8, 7, 6]
    assert model.predicts == 1, "one network pass for the whole sweep"
    assert model.resolves == 4


def test_a_stop_reaches_inside_the_fast_loop_and_keeps_what_it_had():
    """Excludes the sweep that checks for a stop only between whole runs.

    A partial curve the user asked to stop is still evidence; discarding it
    spends their wait for nothing.
    """
    model = _FakeModel([9, 8, 7, 6])
    seen = {"n": 0}

    def stop():
        seen["n"] += 1
        return seen["n"] > 2

    counts = fp._resolve_at_thresholds(model, np.zeros((8, 8)),
                                       [0.1, 0.2, 0.3, 0.4], None, stop, None)
    assert counts == [9, 8]


def test_a_build_without_the_private_resolve_api_declines_rather_than_raising():
    """``_instances_from_prediction`` is private, so a StarDist bump may move
    it. The fast path answers ``None`` and the caller falls back."""

    class NoSplit:
        def predict(self, frame):
            raise AttributeError("no split API in this build")

    assert fp._resolve_at_thresholds(NoSplit(), np.zeros((4, 4)), [0.1],
                                     None, None, None) is None


def test_a_resolve_that_fails_part_way_discards_the_whole_fast_path():
    """Excludes the sweep that mixes points from two code paths.

    Half a curve measured one way and half another is not one measurement,
    whatever the numbers look like.
    """

    class HalfBroken(_FakeModel):
        def _instances_from_prediction(self, *a, **k):
            if self.resolves >= 2:
                raise RuntimeError("signature changed")
            return super()._instances_from_prediction(*a, **k)

    model = HalfBroken([9, 8, 7])
    assert fp._resolve_at_thresholds(model, np.zeros((4, 4)), [0.1, 0.2, 0.3],
                                     None, None, None) is None


def test_tiling_skips_the_fast_path_entirely(monkeypatch):
    """The 5-10x was measured untiled. A fast path used in a configuration
    nobody measured is the branch-that-exists problem (CLAUDE.md §7.7)."""
    monkeypatch.setattr(fp, "missing_stardist_deps", lambda: [])
    seen = []
    monkeypatch.setattr(fp, "segment_frame",
                        lambda arr, **k: (seen.append(k.get("prob_thresh")),
                                          np.full((2, 2), 5, np.int32))[1])
    monkeypatch.setattr(fp, "_resolve_at_thresholds",
                        lambda *a, **k: pytest.fail("fast path used with tiling"))

    counts = fp.sweep_frame(np.zeros((8, 8)), [0.1, 0.2], tile_px=256)
    assert counts == [5, 5]
    assert seen == [0.1, 0.2]


def test_the_slow_path_goes_through_segment_frame(monkeypatch):
    """So a sweep and the Segment button cannot disagree about a threshold."""
    monkeypatch.setattr(fp, "missing_stardist_deps", lambda: [])
    monkeypatch.setattr(fp, "_resolve_at_thresholds", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(fp, "segment_frame",
                        lambda arr, **k: (seen.append(k.get("prob_thresh")),
                                          np.full((2, 2), 4, np.int32))[1])

    assert fp.sweep_frame(np.zeros((8, 8)), [0.3, 0.7]) == [4, 4]
    # The first call is the model-load borrow, then one per threshold.
    assert seen[-2:] == [0.3, 0.7]


def test_a_sweep_without_stardist_says_so_rather_than_returning_nothing(monkeypatch):
    monkeypatch.setattr(fp, "missing_stardist_deps", lambda: ["stardist"])
    with pytest.raises(RuntimeError, match="not installed"):
        fp.sweep_frame(np.zeros((4, 4)), [0.1])


@pytest.mark.parametrize("size,expected", [
    (0, (100, 80)), (40, (40, 40)), (500, (100, 80)), (-3, (100, 80)),
])
def test_centre_crop_never_grows_a_frame_and_never_refuses_one(size, expected):
    arr = np.zeros((100, 80), np.float32)
    assert fp.centre_crop(arr, size).shape == expected
