"""S4 follow-up: Cell Browser inherits the shared per-channel LUT.

The composite scaler uses the shared (lo, hi) window when one exists for the
channel, and falls back to a per-crop 1–99 percentile auto-scale otherwise.
"""
from __future__ import annotations

import numpy as np

from well_viewer.features.cell_browser.worker import _norm01


def test_norm01_uses_shared_bounds_when_given():
    crop = np.array([[0.0, 50.0], [100.0, 200.0]], dtype=np.float32)
    # Fixed window 0..100 → 200 clips to 1.0, 50 → 0.5.
    out = _norm01(crop, bounds=(0.0, 100.0))
    assert out[0, 0] == 0.0
    assert abs(out[0, 1] - 0.5) < 1e-6
    assert out[1, 1] == 1.0   # clipped


def test_norm01_auto_when_no_bounds():
    crop = np.array([[0.0, 50.0], [100.0, 200.0]], dtype=np.float32)
    out = _norm01(crop)            # 1–99 percentile auto
    assert out.min() == 0.0 and out.max() == 1.0


def test_norm01_degenerate_bounds_safe():
    crop = np.array([[5.0, 5.0]], dtype=np.float32)
    out = _norm01(crop, bounds=(5.0, 5.0))  # hi<=lo guarded
    assert np.all(np.isfinite(out))
