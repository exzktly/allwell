"""A clipped pixel is counted at the camera's ceiling, not the file type's.

A 12-bit camera saves ``uint16`` files: its clipped pixels read 4,095 and
nothing reaches 65,535. Measured against the file type, such a frame records a
saturated fraction of exactly 0.0 however much of it is clipped — "not
detectable this way" written as "none". The frames below go through the
pipeline's own reader, because the ceiling it reports is what
``_load_image_group`` passes on.
"""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

from pipeline.field_quality import saturated_fraction
from pipeline.io import load_2d_gray_with_ceiling


def _recorded(tmp_path, frame: np.ndarray) -> float | None:
    path = tmp_path / "exp_DAPI_A01_f1_t0.tif"
    tifffile.imwrite(path, frame)
    arr, ceiling = load_2d_gray_with_ceiling(path)
    return saturated_fraction(arr, dtype_max=ceiling)


def _camera(bits: int, mean: float, seed: int = 0) -> np.ndarray:
    top = 2 ** bits - 1
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(mean, 900, (512, 512)), 0, top).astype(np.uint16)


@pytest.mark.parametrize(("bits", "mean"), [(12, 3000.0), (14, 15000.0), (10, 600.0)])
def test_a_frame_clipped_below_the_file_type_records_its_clipped_share(tmp_path, bits, mean):
    frame = _camera(bits, mean)
    truly_clipped = float((frame == 2 ** bits - 1).mean())
    assert truly_clipped > 0.002          # the premise: Review would flag this frame
    assert _recorded(tmp_path, frame) == pytest.approx(truly_clipped, abs=1e-12)


def test_a_frame_that_reaches_no_ceiling_records_zero(tmp_path):
    """The control. Its maximum is not ``2**n - 1``, so nothing is clipped."""
    frame = np.clip(np.random.default_rng(1).normal(1500, 200, (512, 512)), 0, 3000)
    frame = frame.astype(np.uint16)
    frame[0, 0] = 3000                    # the brightest pixel, well short of 4,095
    assert _recorded(tmp_path, frame) == 0.0


def test_a_true_16_bit_frame_is_still_counted_at_65535(tmp_path):
    frame = np.full((100, 100), 20000, np.uint16)
    frame[:5, :] = 65535
    frame[50, 50] = 4095                  # a 12-bit-looking value below the real ceiling
    assert _recorded(tmp_path, frame) == pytest.approx(0.05)


def test_a_coincidental_power_of_two_maximum_costs_one_pixel(tmp_path):
    """A dim frame whose one brightest pixel reads 4,095 is 1 / N, not a flag."""
    frame = np.full((512, 512), 800, np.uint16)
    frame[10, 10] = 4095
    assert _recorded(tmp_path, frame) == pytest.approx(1 / 512 ** 2)


def test_a_mask_is_not_a_clipped_frame(tmp_path):
    """A binary or label image peaks at ``2**n - 1`` for a small n; that is not clipping."""
    mask = np.zeros((64, 64), np.uint16)
    mask[:32] = 1
    assert _recorded(tmp_path, mask) == 0.0
    labels = np.zeros((64, 64), np.uint16)
    labels[:32] = 127
    assert _recorded(tmp_path, labels) == 0.0


def test_a_float_frame_still_has_no_ceiling(tmp_path):
    assert _recorded(tmp_path, np.full((32, 32), 4095.0, np.float32)) is None
