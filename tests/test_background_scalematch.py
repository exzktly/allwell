"""Empty-well background subtraction must remove the measured field's spatial
shape (e.g. corner-bright) even when the sample image differs from the
reference well in exposure / offset — i.e. no residual that tracks the field.
"""
from __future__ import annotations

import numpy as np

import process_microscopy as pm


def _corner_bright_field(h=80, w=80, center=100.0, corner=400.0):
    """A background field that is BRIGHTER at the corners than the center
    (the symptom reported: corners several× the center)."""
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    d = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
    d = d / d.max()
    return (center + (corner - center) * d).astype(np.float32)


def _corner_center(arr):
    h, w = arr.shape
    corner = float(arr[0, 0])
    center = float(arr[h // 2, w // 2])
    return corner, center


def test_field_is_corner_bright():
    B = _corner_bright_field()
    corner, center = _corner_center(B)
    assert corner > 3 * center  # ~4x, matching the report


def test_raw_subtraction_leaves_corner_residual_on_exposure_mismatch():
    # Baseline: the OLD behaviour (subtract raw B) leaves a corner-bright
    # residual when the image's background is 1.5x the reference field.
    B = _corner_bright_field()
    img = (1.5 * B).astype(np.float32)
    raw = np.clip(img - B, 0.0, None)
    corner, center = _corner_center(raw)
    assert corner > 2 * center  # residual still corner-bright → the bug


def test_scalematch_flattens_corner_bright_background():
    B = _corner_bright_field()
    # Sample image: background = 1.5*B + 30 (exposure mismatch + dark offset),
    # plus a few bright "cells" the fit must ignore.
    rng = np.random.default_rng(0)
    img = (1.5 * B + 30.0).astype(np.float32)
    for _ in range(20):
        r = rng.integers(0, 80); c = rng.integers(0, 80)
        img[r, c] += 5000.0
    out = pm.subtract_background(img, 0, "empty_well", B)
    corner, center = _corner_center(out)
    # Background is now flat: corner ≈ center (no field-shaped residual).
    assert abs(corner - center) < 0.05 * max(center, 1.0) + 5.0
    # And it didn't nuke everything to zero — the background pedestal subtracts
    # to ~0 but the fit shouldn't go wildly negative.
    assert np.isfinite(out).all()


def test_matched_exposure_is_unchanged_behaviour():
    # When exposure matches (a≈1, c≈0) the result still removes B to ~0.
    B = _corner_bright_field()
    img = B.copy()
    out = pm.subtract_background(img, 0, "empty_well", B)
    assert float(np.abs(out).mean()) < 1.0


def test_fit_ignores_bright_cells_for_scale():
    # The multiplicative scale is recovered from background pixels, not cells.
    B = _corner_bright_field()
    img = (2.0 * B).astype(np.float32)
    img[10:14, 10:14] += 9000.0  # a bright cell cluster
    fitted = pm._match_background_scale(img, B)
    # fitted ≈ 2*B over the field → ratio of fitted/B ~ 2 at a background pixel
    assert 1.7 < float(fitted[0, 0] / B[0, 0]) < 2.3
