"""Log-scale image display helper for the Image Table."""
from __future__ import annotations

import numpy as np

from well_viewer.core.ui_helpers import apply_log_scale, lut_colormap_to_rgb


def test_apply_log_scale_floors_nonpositive_and_log10s():
    a = np.array([[0.0, 1.0, 10.0], [100.0, 1000.0, -5.0]], dtype=float)
    out, lo, hi = apply_log_scale(a, 10.0, 1000.0)
    # smallest positive value is 1.0 → floor 1.0 → log10(1)=0 for 0 and -5.
    assert np.allclose(out, [[0.0, 0.0, 1.0], [2.0, 3.0, 0.0]])
    assert lo == 1.0 and hi == 3.0


def test_apply_log_scale_passes_none_bounds_through():
    a = np.array([1.0, 10.0, 100.0], dtype=float)
    out, lo, hi = apply_log_scale(a, None, None)
    assert lo is None and hi is None
    assert np.allclose(out, [0.0, 1.0, 2.0])


def test_apply_log_scale_all_nonpositive_uses_unit_floor():
    a = np.array([0.0, -1.0, -2.0], dtype=float)
    out, _lo, _hi = apply_log_scale(a, None, None)
    assert np.allclose(out, 0.0)  # all floored to 1.0 → log10(1)=0


def test_lut_colormap_log_scale_differs_from_linear():
    a = np.array([[1.0, 10.0], [100.0, 1000.0]], dtype=float)
    lin = lut_colormap_to_rgb(a, None, None, "")
    log = lut_colormap_to_rgb(a, None, None, "", log_scale=True)
    assert lin.shape == log.shape == (2, 2, 3)
    assert not np.array_equal(lin, log)
    # On a log scale the evenly-spaced decades map to evenly-spaced grey levels.
    grey = log[..., 0].ravel()
    # vals [1,10,100,1000] → log10 [0,1,2,3] → norm [0,1/3,2/3,1] → *255 with
    # uint8 truncation: 1/3*255 = 84.99 → 84.
    assert sorted(grey.tolist()) == [0, 84, 170, 255]
