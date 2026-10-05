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


@pytest.fixture
def stardist_present(monkeypatch):
    """Report the dependencies as installed, without installing them.

    ``sweep_frame`` names a missing install ahead of every other check —
    including the flat-frame refusal — because it is the reason nothing works
    rather than a property of the picture. That is right for the app and it
    means a test about the sweep's own logic has to say which of the two
    conditions it is exercising.
    """
    monkeypatch.setattr(FP, "missing_stardist_deps", lambda: [])


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
    import pipeline.stardist_cache as stardist_cache

    monkeypatch.setattr(FP, "_GUI_STARDIST_MODEL", None)
    loads: list[str] = []
    monkeypatch.setattr(stardist_cache, "_ensure_pkg_resources_shim", lambda: None)
    monkeypatch.setattr(stardist_cache, "_load_stardist_pretrained",
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
        self.seen = []

    def predict(self, frame):
        # **The assertion that would have caught the blocker.** The first
        # version of this fake ignored `frame` entirely, so the fast path could
        # be — and was — handed raw camera counts, where StarDist's `predict`
        # takes `normalizer=None` and assumes its input is already normalised.
        # On a real frame it returned 6 nuclei falling to 4 where the truth was
        # 86 falling to 0, and drew a flat line rather than declining.
        arr = np.asarray(frame)
        assert float(arr.min()) >= 0.0 and float(arr.max()) <= 1.0, (
            f"the network was handed un-normalised input "
            f"(range {float(arr.min())}..{float(arr.max())})")
        self.seen.append(arr)
        self.predicts += 1
        # Deliberately NOT ``arr.shape``: StarDist's grid subsamples the
        # probability map, so the two really are different sizes in
        # production, and a fake whose prob matched the frame would make the
        # shape assertion below satisfiable by passing either one (§7.8
        # rule 2 — build the fixture so right and wrong differ).
        h, w = arr.shape[0] // 2, arr.shape[1] // 2
        return (np.zeros((h, w)), np.zeros((h, w, 32)))

    def _instances_from_prediction(self, shape, prob, dist, *, prob_thresh,
                                   nms_thresh=None):
        # **The shape is the canvas the polygons are rasterised onto**, and a
        # wrong one is not an error: ``polygons_to_label`` calls
        # ``skimage.draw.polygon(*c, shape)``, which *clips* an out-of-bounds
        # polygon to nothing. Hand it the grid-subsampled ``prob.shape``
        # instead of the frame's and every nucleus whose centre falls outside
        # the halved canvas is silently dropped — a smaller count, no
        # exception, no marker. Ignoring the argument here, as this fake first
        # did, made that substitution invisible to every test in the file.
        assert shape == np.asarray(self.seen[-1]).shape, (
            f"resolved onto {shape}, not the frame's own "
            f"{np.asarray(self.seen[-1]).shape}")
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


def _varied(seed=0, scale=3000.0):
    """A frame with real dynamic range, in camera counts — what the app hands
    the sweep. A flat or already-normalised fixture cannot tell a preparing
    implementation from one that skips it."""
    r = np.random.default_rng(seed)
    return (r.random((16, 16)) * scale + 300.0).astype(np.float32)


def test_the_slow_path_goes_through_segment_frame(monkeypatch, stardist_present):
    """So a sweep and the Segment button cannot disagree about a threshold."""
    monkeypatch.setattr(fp, "ensure_model", lambda: object())
    monkeypatch.setattr(fp, "_resolve_at_thresholds", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(fp, "segment_frame",
                        lambda arr, **k: (seen.append(k.get("prob_thresh")),
                                          np.full((2, 2), 4, np.int32))[1])

    assert fp.sweep_frame(_varied(), [0.3, 0.7]) == [4, 4]
    assert seen == [0.3, 0.7], "one call per threshold, and no discarded borrow"


def test_the_sweep_prepares_the_frame_before_the_network_sees_it(monkeypatch, stardist_present):
    """**The blocker.** `model.predict` takes `normalizer=None` and assumes its
    input is already normalised; nothing enforces it.

    Excludes the implementation that hands the corrected frame — raw camera
    counts — straight to the network. Measured against the Segment button on a
    real frame, that returned 6 nuclei falling to 4 where the truth was 86
    falling to 0, did not decline, and drew a flat line the manual tells the
    reader means the threshold does not matter.
    """
    model = _FakeModel([9, 8])
    monkeypatch.setattr(fp, "ensure_model", lambda: model)

    raw = _varied(scale=50_000.0)
    assert raw.max() > 1.0, "the fixture must be able to expose the defect"
    fp.sweep_frame(raw, [0.1, 0.2])

    assert model.seen, "the network was never reached"
    got = model.seen[0]
    assert float(got.max()) <= 1.0 and float(got.min()) >= 0.0


def test_a_near_constant_field_is_refused_rather_than_swept(monkeypatch, stardist_present):
    """Excludes the sweep that runs nineteen unguarded passes over exactly the
    input the guard exists for.

    A near-constant field is the classic trigger for StarDist's uncatchable
    ClipperLib abort — a C++ ``terminate()``, not an exception — which in this
    process takes the whole application down rather than one worker.
    """
    monkeypatch.setattr(
        fp, "ensure_model",
        lambda: pytest.fail("loaded a model for a frame it then refused"))
    monkeypatch.setattr(fp, "_resolve_at_thresholds",
                        lambda *a, **k: pytest.fail("swept a near-constant field"))
    monkeypatch.setattr(fp, "segment_frame",
                        lambda *a, **k: pytest.fail("segmented a near-constant field"))

    flat = np.full((16, 16), 512.0, np.float32)
    # ``None``, not nineteen zeros. Zeros say the network ran at every
    # threshold and found nothing, which is a statement about detection; this
    # frame has no dynamic range and the network never ran. The wrong
    # implementation that passes "it declined" is the one that returned
    # ``[0] * len(grid)`` and had the user told "No nuclei were detected at
    # any threshold in the sweep" about a measurement never taken.
    assert fp.sweep_frame(flat, [0.1, 0.2, 0.3]) is None

    # And the refusal comes BEFORE the model load, which is the difference
    # between declining in a moment and declining after a 10-30 s TensorFlow
    # import — the ``ensure_model`` stub above fails the test if it is
    # reached, which the previous version of this assertion could not do: it
    # was written ``pytest.fail(...) if False else object()``, a branch that
    # can never run, so it passed against the shipped order.


def test_an_empty_grid_loads_no_model_and_runs_no_inference(monkeypatch, stardist_present):
    """Nothing asked for is nothing to load a model for."""
    monkeypatch.setattr(fp, "ensure_model",
                        lambda: pytest.fail("loaded a model for an empty sweep"))
    assert fp.sweep_frame(_varied(), []) == []


def test_a_sweep_without_stardist_says_so_rather_than_returning_nothing(monkeypatch):
    monkeypatch.setattr(fp, "missing_stardist_deps", lambda: ["stardist"])
    with pytest.raises(RuntimeError, match="not installed"):
        fp.sweep_frame(np.zeros((4, 4)), [0.1])


