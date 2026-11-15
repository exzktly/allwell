"""The pipeline's image reader returns a frame, never a row of one.

``tifffile`` hands an RGB image back as ``(H, W, 3)``. Reducing that the way a
``(Z, H, W)`` stack is reduced — index 0 until two axes are left — yields the
image's first row, a ``(W, 3)`` strip that segmentation and quantification then
treat as the field. The grouper accepts such files, so the reader has to know
the difference.
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest
import tifffile

from pipeline.empty_well import _load_image_source
from pipeline.io import load_2d_gray_with_ceiling

H, W = 48, 64


def _grey() -> np.ndarray:
    grey = np.zeros((H, W), np.uint8)
    grey[10:20, 10:30] = 200          # not symmetric, so a transposed read differs
    grey[40, 5] = 77
    return grey


def test_a_grey_image_saved_as_rgb_reads_as_that_grey_image(tmp_path):
    grey = _grey()
    path = tmp_path / "exp_GFP_A01_f1_t0.tif"
    tifffile.imwrite(path, np.stack([grey, grey, grey], axis=-1), photometric="rgb")
    frame, ceiling = load_2d_gray_with_ceiling(path)
    assert frame.shape == (H, W)      # was (W, 3): the image's first row
    np.testing.assert_array_equal(frame, grey.astype(np.float32))
    assert ceiling == 255.0


def test_an_alpha_plane_is_ignored():
    from pipeline.io import first_2d_plane

    grey = _grey()
    rgba = np.stack([grey, grey, grey, np.full_like(grey, 255)], axis=-1)
    np.testing.assert_array_equal(first_2d_plane(rgba), grey)


def test_a_coloured_image_is_refused_by_name(tmp_path):
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[10:20, 10:30, 1] = 200        # a green square: the planes differ
    path = tmp_path / "exp_GFP_A01_f1_t0.tif"
    tifffile.imwrite(path, rgb, photometric="rgb")
    with pytest.raises(ValueError, match="colour image") as caught:
        frame, _ = load_2d_gray_with_ceiling(path)
        assert frame.shape == (H, W), f"read as a {frame.shape} strip"
    message = str(caught.value)
    assert "exp_GFP_A01_f1_t0.tif" in message
    assert f"{W} x {H}" in message


def test_a_stack_still_gives_its_first_page():
    from pipeline.io import first_2d_plane

    stack = np.arange(5 * H * W, dtype=np.uint16).reshape(5, H, W)
    np.testing.assert_array_equal(first_2d_plane(stack), stack[0])
    three = np.arange(3 * H * W, dtype=np.uint16).reshape(3, H, W)
    np.testing.assert_array_equal(first_2d_plane(three), three[0])


def test_a_blank_well_frame_inside_an_archive_is_read_the_same_way(tmp_path):
    """The empty-well sampler reads archive members itself; it uses the one rule."""
    grey = _grey()
    coloured = np.zeros((H, W, 3), np.uint8)
    coloured[10:20, 10:30, 0] = 200
    zpath = tmp_path / "H12.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("H12/", "")
        zf.writestr(".DS_Store", b"\x00\x00\x00\x01Bud1")
        zf.writestr("__MACOSX/H12/._exp_GFP_H12_f1_t0.tif", b"\x00\x05\x16\x07")
        for name, arr in (("H12/exp_GFP_H12_f1_t0.tif", np.stack([grey] * 3, axis=-1)),
                          ("H12/exp_GFP_H12_f2_t0.tif", coloured)):
            buf = io.BytesIO()
            tifffile.imwrite(buf, arr, photometric="rgb")
            zf.writestr(name, buf.getvalue())
    frame = _load_image_source((zpath, "H12/exp_GFP_H12_f1_t0.tif"))
    assert frame.shape == (H, W)
    np.testing.assert_array_equal(frame, grey.astype(np.float32))
    # A coloured blank has no grey value to sample: skipped, not read as a strip.
    assert _load_image_source((zpath, "H12/exp_GFP_H12_f2_t0.tif")) is None
