"""Tunable StarDist detection thresholds.

``prob_thresh`` (the object-probability cut) and ``nms_thresh`` (the polygon
overlap cut) were previously fixed at whatever the pretrained weights shipped,
so an under- or over-segmenting plate had nothing to turn. They are now
:class:`pipeline.stardist.StarDistParams` fields, installed per worker from the
``--stardist_*`` CLI args and passed to ``predict_instances``.

Two properties matter most and are asserted here:

* the DEFAULT call must be unchanged — a params value with both thresholds unset
  passes neither keyword, so every existing dataset re-runs identically;
* "auto" has exactly ONE spelling. The CLI's ``-1`` sentinel, a blank form
  field, and the literal word ``auto`` all resolve through
  :func:`pipeline.stardist.parse_threshold`, so the three entry points cannot
  drift into disagreeing about what "the model's own default" means.

Pure logic — the model is a stub, so no TensorFlow / StarDist install is needed.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

sd = pytest.importorskip("pipeline.stardist")


@pytest.fixture(autouse=True)
def _reset_params():
    sd.set_params(None)
    yield
    sd.set_params(None)


@pytest.fixture(autouse=True)
def _stub_csbdeep(monkeypatch):
    """Stand in for ``csbdeep.utils.normalize`` so the fast lane needs no TF.

    Only installed when csbdeep is genuinely absent — on a machine that has the
    real stack the real function is used, so this can never mask a change in
    what the pipeline actually calls.
    """
    if "csbdeep.utils" in sys.modules:
        return
    try:
        import csbdeep.utils  # noqa: F401
        return
    except ImportError:
        pass

    def _normalize(a, lo, hi, clip=False):
        a = np.asarray(a, dtype=np.float32)
        p_lo, p_hi = np.percentile(a, lo), np.percentile(a, hi)
        out = (a - p_lo) / max(float(p_hi - p_lo), 1e-9)
        return np.clip(out, 0.0, 1.0) if clip else out

    pkg = types.ModuleType("csbdeep")
    utils = types.ModuleType("csbdeep.utils")
    utils.normalize = _normalize
    pkg.utils = utils
    monkeypatch.setitem(sys.modules, "csbdeep", pkg)
    monkeypatch.setitem(sys.modules, "csbdeep.utils", utils)


class _FakeModel:
    """Records the kwargs ``segment_nuclei`` hands to ``predict_instances``."""

    def __init__(self):
        self.calls: list[dict] = []

    def predict_instances(self, img, **kwargs):
        self.calls.append(kwargs)
        return np.zeros(np.shape(img), dtype=np.int32), None


def _frame(seed: int = 0, shape=(64, 64)):
    return (np.random.default_rng(seed).random(shape).astype(np.float32) * 1000.0)


# ── parse_threshold: one rule for CLI, form and Playground ───────────────────

@pytest.mark.parametrize("value", [None, "", "   ", "auto", "AUTO", -1.0, -0.5,
                                   "-1", "not a number"])
def test_absent_or_sentinel_means_the_models_own_default(value):
    assert sd.parse_threshold(value) is None


@pytest.mark.parametrize(("value", "expected"),
                         [(0, 0.0), ("0", 0.0), (0.25, 0.25), ("0.5", 0.5),
                          (1, 1.0), ("1.0", 1.0)])
def test_in_range_values_pass_through(value, expected):
    assert sd.parse_threshold(value) == pytest.approx(expected)


def test_above_one_is_rejected_not_clamped():
    """Clamping would segment the plate with a threshold nobody chose."""
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        sd.parse_threshold(1.5)


# ── StarDistParams ───────────────────────────────────────────────────────────

def test_defaults_are_model_defaults_and_no_tiling():
    p = sd.DEFAULT_STARDIST_PARAMS
    assert (p.prob_thresh, p.nms_thresh, p.tile_px) == (None, None, 0)
    assert sd.get_params() == p


def test_set_params_none_restores_the_defaults():
    sd.set_params(sd.StarDistParams(prob_thresh=0.9, tile_px=512))
    sd.set_params(None)
    assert sd.get_params() == sd.DEFAULT_STARDIST_PARAMS


# ── segment_nuclei: what actually reaches predict_instances ──────────────────

def test_default_params_pass_no_thresholds_at_all():
    """The unchanged-behaviour guarantee: an untuned run makes the same call it
    made before the thresholds were exposed."""
    model = _FakeModel()
    sd.segment_nuclei(_frame(), model=model)
    assert model.calls == [{}]


def test_each_threshold_is_forwarded_only_when_set():
    model = _FakeModel()
    sd.segment_nuclei(_frame(), model=model,
                      params=sd.StarDistParams(prob_thresh=0.2))
    sd.segment_nuclei(_frame(), model=model,
                      params=sd.StarDistParams(nms_thresh=0.4))
    sd.segment_nuclei(_frame(), model=model,
                      params=sd.StarDistParams(prob_thresh=0.2, nms_thresh=0.4,
                                               tile_px=32))
    assert model.calls == [
        {"prob_thresh": 0.2},
        {"nms_thresh": 0.4},
        {"n_tiles": (2, 2), "prob_thresh": 0.2, "nms_thresh": 0.4},
    ]


def test_zero_is_a_real_threshold_not_an_absent_one():
    """``0.0`` detects everything; it must not be confused with "unset"."""
    model = _FakeModel()
    sd.segment_nuclei(_frame(), model=model,
                      params=sd.StarDistParams(prob_thresh=0.0))
    assert model.calls == [{"prob_thresh": 0.0}]


def test_a_near_constant_field_is_refused_before_prediction():
    """The ClipperLib guard still runs on the tunable path — an empty field is
    0 nuclei, never a call into the network that could abort the process."""
    model = _FakeModel()
    out = sd.segment_nuclei(np.full((16, 16), 7.0, dtype=np.float32), model=model,
                            params=sd.StarDistParams(prob_thresh=0.1))
    assert model.calls == []
    assert out.shape == (16, 16)
    assert out.max() == 0


def test_no_model_is_an_error_not_an_empty_result():
    with pytest.raises(RuntimeError, match="not initialised"):
        sd.segment_nuclei(_frame(), model=None)


def test_worker_globals_drive_the_pipeline_entry_point(monkeypatch):
    """``_segment_stardist_nuclei`` is the same inference, reading the installed
    model + params — so the pipeline and an interactive caller cannot diverge."""
    model = _FakeModel()
    sd.set_model(model)
    sd.set_params(sd.StarDistParams(prob_thresh=0.33, nms_thresh=0.66))
    try:
        sd._segment_stardist_nuclei(_frame())
    finally:
        sd.set_model(None)
    assert model.calls == [{"prob_thresh": 0.33, "nms_thresh": 0.66}]
