"""StarDist input must be finite AND in [0, 1] before predict_instances.

StarDist's polygon NMS (ClipperLib) hard-aborts the process with an uncatchable
C++ exception ("ClipperLib::clipperException: Coordinate outside allowed range")
when the network sees out-of-distribution input — either NaN/Inf (one NaN
poisons the percentile normalization) or huge *finite* outliers (empty-well
flat-field divides by a near-zero field and amplifies a region ~100x; the
default normalize(clip=False) leaves those at the top). ``_segment_stardist_nuclei``
must scrub non-finite values AND clamp to [0, 1] so a real run can't hit the
abort. Uses a fake model, so no TensorFlow / StarDist needed.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

# The StarDist segmenters + per-worker model state moved here (pm re-exports the
# functions); the model global must be patched where it actually lives.
import pipeline.stardist as _stardist
from pipeline import stardist


@pytest.fixture(autouse=True)
def fake_csbdeep(monkeypatch):
    """Provide csbdeep.utils.normalize (absent in the sandbox) faithfully: it
    uses np.percentile — the *non* nan-aware form — so a single NaN poisons the
    whole field, exactly the behaviour that makes the guard necessary."""
    def normalize(x, pmin=3, pmax=99.8, axis=None, clip=False, eps=1e-20,
                  dtype=np.float32):
        x = np.asarray(x, dtype=np.float32)
        lo = np.percentile(x, pmin)
        hi = np.percentile(x, pmax)
        out = (x - lo) / (hi - lo + eps)
        if clip:                       # csbdeep 0.8.0 hard-clips to [0, 1]
            out = np.clip(out, 0.0, 1.0)
        return out.astype(dtype)

    utils = types.ModuleType("csbdeep.utils")
    utils.normalize = normalize
    mod = types.ModuleType("csbdeep")
    mod.utils = utils
    monkeypatch.setitem(sys.modules, "csbdeep", mod)
    monkeypatch.setitem(sys.modules, "csbdeep.utils", utils)


class _FiniteOnlyModel:
    """Stand-in StarDist model that *requires* finite input (mirrors the real
    extension, which would otherwise abort the process)."""

    def __init__(self):
        self.seen = None

    def predict_instances(self, img):
        assert np.all(np.isfinite(img)), "predict_instances got non-finite input"
        assert img.min() >= -1e-6 and img.max() <= 1.0 + 1e-6, \
            f"predict_instances got out-of-[0,1] input: [{img.min()}, {img.max()}]"
        self.seen = img
        labels = np.zeros(img.shape, dtype=np.int32)
        labels[2:5, 2:5] = 1
        return labels, {}


@pytest.fixture
def fake_model(monkeypatch):
    m = _FiniteOnlyModel()
    monkeypatch.setattr(_stardist, "_STARDIST_MODEL", m)
    return m


def test_nan_input_is_scrubbed(fake_model):
    img = np.random.default_rng(0).random((16, 16)).astype(np.float32) * 100
    img[7, 7] = np.nan          # one NaN would poison percentile normalization
    img[1, 1] = np.inf
    img[2, 2] = -np.inf
    labels = stardist._segment_stardist_nuclei(img)       # must NOT raise/abort
    assert labels.dtype == np.int32
    assert np.all(np.isfinite(fake_model.seen))
    assert labels.max() == 1


def test_all_nan_input_skips_prediction(fake_model):
    # All-NaN scrubs to all-zero → near-constant → the degenerate-field guard
    # returns empty labels WITHOUT calling StarDist (avoids the ClipperLib abort).
    img = np.full((12, 12), np.nan, dtype=np.float32)
    labels = stardist._segment_stardist_nuclei(img)
    assert labels.shape == img.shape
    assert labels.max() == 0
    assert fake_model.seen is None          # prediction skipped


def test_constant_field_skips_prediction(fake_model):
    # A near-constant field (e.g. an empty-well over-subtracted nuclear channel)
    # has no nuclei and is the classic ClipperLib trigger — skip it.
    img = np.full((20, 20), 123.0, dtype=np.float32)
    labels = stardist._segment_stardist_nuclei(img)
    assert labels.max() == 0
    assert fake_model.seen is None


def test_finite_structured_input_reaches_model(fake_model):
    img = np.random.default_rng(1).random((10, 10)).astype(np.float32)
    stardist._segment_stardist_nuclei(img)
    assert fake_model.seen is not None and np.all(np.isfinite(fake_model.seen))


def test_ood_amplified_spike_is_clamped(fake_model):
    # The empty-well failure mode: a dim region amplified to a huge FINITE value.
    # Without clip=True + the explicit clamp this reaches StarDist and overflows
    # ClipperLib; the guard must hand the model only values in [0, 1].
    rng = np.random.default_rng(2)
    img = (rng.random((24, 24)).astype(np.float32) * 0.5 + 0.25)   # structured
    img[5, 5] = 1e6           # amplified outlier
    img[6, 6] = 5e5
    labels = stardist._segment_stardist_nuclei(img)      # must NOT raise/abort
    assert labels.dtype == np.int32
    assert fake_model.seen is not None
    assert fake_model.seen.min() >= -1e-6 and fake_model.seen.max() <= 1.0 + 1e-6
