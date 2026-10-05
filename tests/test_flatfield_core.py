"""flatfield_core: the Qt-free flat-field estimators shared by the pipeline and
the Segmentation-tab validation view. Guards the extraction from
process_microscopy.py and the mean-1 / floor / divide contracts.
"""
from __future__ import annotations

import numpy as np

import pipeline.flatfield as fc
from pipeline import flatfield


def test_normalize_is_mean_one_and_floored():
    field = np.linspace(1.0, 5.0, 64, dtype=np.float32).reshape(8, 8)
    out = fc.normalize_flatfield(field)
    assert out is not None
    assert abs(float(out.mean()) - 1.0) < 1e-4
    assert float(out.min()) >= fc.FLATFIELD_FLOOR - 1e-6


def test_normalize_floor_clips_extreme_dynamic_range():
    field = np.ones((8, 8), dtype=np.float32) * 100.0
    field[0, 0] = 0.001  # would divide to a huge amplification without the floor
    out = fc.normalize_flatfield(field)
    assert float(out.min()) >= fc.FLATFIELD_FLOOR - 1e-6


def test_normalize_degenerate_returns_none():
    assert fc.normalize_flatfield(np.zeros((4, 4), dtype=np.float32)) is None


def test_gaussian_field_is_mean_one_same_shape():
    img = np.random.default_rng(0).random((40, 40)).astype(np.float32) * 100
    g = fc.estimate_gaussian_flatfield(img)
    assert g is not None and g.shape == img.shape
    assert abs(float(g.mean()) - 1.0) < 1e-3


def test_basic_field_from_stack_and_edge_cases():
    rng = np.random.default_rng(1)
    stack = [rng.random((16, 16)).astype(np.float32) for _ in range(5)]
    b = fc.estimate_basic_flatfield(stack)
    assert b is not None and abs(float(b.mean()) - 1.0) < 1e-3
    assert fc.estimate_basic_flatfield([]) is None
    # mismatched shapes are filtered to the first shape's frames
    mixed = [np.ones((8, 8), np.float32), np.ones((4, 4), np.float32)]
    assert fc.estimate_basic_flatfield(mixed).shape == (8, 8)


def test_apply_flatfield_divides_and_noops():
    img = np.full((8, 8), 10.0, dtype=np.float32)
    field = np.full((8, 8), 2.0, dtype=np.float32)
    out = fc.apply_flatfield(img, field)
    assert np.allclose(out, 5.0)
    # None / shape-mismatch are no-ops (return the input)
    assert fc.apply_flatfield(img, None) is img
    assert fc.apply_flatfield(img, np.ones((4, 4), np.float32)) is img


def test_gaussian_roundtrip_flattens_a_smooth_gradient():
    # Image must be large relative to FLATFIELD_SIGMA (50) for the blurred field
    # to track a field-scale ramp; a ~σ-sized image blurs to a flat field.
    yy, xx = np.mgrid[0:256, 0:256]
    grad = (100.0 + xx + yy).astype(np.float32)  # smooth illumination ramp
    corrected = fc.apply_flatfield(grad, fc.estimate_gaussian_flatfield(grad))
    # Compare the interior (the blur's reflect edges leave a boundary band that
    # isn't representative). The ramp's relative spread collapses there.
    inner_c = corrected[60:-60, 60:-60]
    inner_g = grad[60:-60, 60:-60]
    assert inner_c.std() / inner_c.mean() < 0.1 * (inner_g.std() / inner_g.mean())


def test_pipeline_reexports_the_same_functions():
    assert flatfield.estimate_gaussian_flatfield is fc.estimate_gaussian_flatfield
    assert flatfield.estimate_basic_flatfield is fc.estimate_basic_flatfield
    assert flatfield.apply_flatfield is fc.apply_flatfield
    assert flatfield._FLATFIELD_SIGMA == fc.FLATFIELD_SIGMA
