"""The two object-size-agnostic background methods — ``fft_bandpass`` (2-D FFT
Butterworth high-pass) and ``robust_mesh`` (SExtractor/Background2D-style grid)
— must flatten a strongly uneven background that the morphological ``sliding_disk``
leaves behind, while preserving bright cell signal and never producing
non-finite / negative output.
"""
from __future__ import annotations

import numpy as np
import pytest

from pipeline import background


def _scene(h=512, w=512, seed=0):
    """A hard scene that defeats a fixed-radius morphological method: a radial
    illumination gradient PLUS several broad background blobs ~250-450 px across
    — wider than the radius-100 structuring element, so a rolling-ball / disk
    can't sit under them to reach the true baseline and leaves residual. Small
    sharp Gaussian "cells" (~4 px) must survive. Returns (image, cell_centers)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    r = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
    background = 200.0 + 500.0 * (1.0 - r / r.max())     # bright center, dim edges
    # Broad background blobs, each far larger than the radius-100 disk — the
    # morphological failure mode (uneven background at scales > the footprint).
    for (by, bx, amp, sig) in [(140, 360, 700.0, 110.0),
                               (380, 150, 600.0, 90.0),
                               (300, 380, 500.0, 130.0)]:
        br = (yy - by) ** 2 + (xx - bx) ** 2
        background = background + amp * np.exp(-br / (2.0 * sig ** 2))
    img = background.astype(np.float32)
    centers = []
    for _ in range(12):
        r0 = int(rng.integers(40, h - 40))
        c0 = int(rng.integers(40, w - 40))
        g = np.exp(-(((yy - r0) ** 2 + (xx - c0) ** 2) / (2.0 * 4.0 ** 2)))
        img = img + 3000.0 * g.astype(np.float32)        # sharp, ~4 px cells
        centers.append((r0, c0))
    return img.astype(np.float32), centers


def _bg_flatness(corrected, centers, h, w, exclude=12):
    """Std of the corrected image over background-only pixels (cells masked)."""
    mask = np.ones((h, w), dtype=bool)
    yy, xx = np.mgrid[0:h, 0:w]
    for (r0, c0) in centers:
        mask &= ~(((yy - r0) ** 2 + (xx - c0) ** 2) <= exclude ** 2)
    return float(np.std(corrected[mask]))


@pytest.mark.parametrize("method", ["robust_mesh", "fft_bandpass"])
def test_finite_nonnegative(method):
    img, _ = _scene()
    out = background.subtract_background(img, 100, method)
    assert out.dtype == np.float32
    assert np.isfinite(out).all()
    assert (out >= 0).all()


@pytest.mark.parametrize("method", ["robust_mesh", "fft_bandpass"])
def test_flatter_than_sliding_disk(method):
    img, centers = _scene()
    h, w = img.shape
    base = background.subtract_background(img, 100, "sliding_disk")
    new = background.subtract_background(img, 100, method)
    f_base = _bg_flatness(base, centers, h, w)
    f_new = _bg_flatness(new, centers, h, w)
    # The new method must leave a *substantially* flatter background.
    assert f_new < 0.7 * f_base, (method, f_new, f_base)


@pytest.mark.parametrize("method", ["robust_mesh", "fft_bandpass"])
def test_cells_survive(method):
    img, centers = _scene()
    out = background.subtract_background(img, 100, method)
    # Each injected cell must remain clearly above the local background.
    for (r0, c0) in centers:
        peak = float(out[r0 - 2:r0 + 3, c0 - 2:c0 + 3].max())
        assert peak > 300.0, (method, r0, c0, peak)


def test_fft_lowpass_reduces_noise():
    # Adding a low-pass (small_px > 0) must reduce high-frequency noise std
    # relative to the pure high-pass on a noisy flat field.
    rng = np.random.default_rng(1)
    img = (500.0 + rng.normal(0, 40.0, size=(256, 256))).astype(np.float32)
    highpass = background.subtract_background(img, 100, "fft_bandpass", fft_small=0)
    bandpass = background.subtract_background(img, 100, "fft_bandpass", fft_small=4)
    assert float(np.std(bandpass)) < float(np.std(highpass))


def test_mesh_defaults_unchanged():
    # The new mesh_ratio / mesh_sigma defaults must reproduce the prior
    # hard-coded behaviour (radius/3 tiles, 3-sigma clip) exactly.
    img, _ = _scene()
    default = background.subtract_background(img, 100, "robust_mesh")
    explicit = background.subtract_background(img, 100, "robust_mesh",
                                      mesh_ratio=3.0, mesh_sigma=3.0)
    assert np.array_equal(default, explicit)


def test_mesh_ratio_changes_tile_scale():
    # A different tile ratio changes the tile size and therefore the estimated
    # background — the output must actually differ from the default.
    img, _ = _scene()
    default = background.subtract_background(img, 100, "robust_mesh", mesh_ratio=3.0)
    coarser = background.subtract_background(img, 100, "robust_mesh", mesh_ratio=6.0)
    assert not np.array_equal(default, coarser)
    assert np.isfinite(coarser).all() and (coarser >= 0).all()


def test_mesh_sigma_changes_clipping():
    # A tighter sigma-clip rejects more pixels per tile and changes the result.
    img, _ = _scene()
    default = background.subtract_background(img, 100, "robust_mesh", mesh_sigma=3.0)
    tighter = background.subtract_background(img, 100, "robust_mesh", mesh_sigma=1.5)
    assert not np.array_equal(default, tighter)
    assert np.isfinite(tighter).all() and (tighter >= 0).all()


def test_mesh_param_guards():
    # Non-positive ratio / sigma fall back to the 3.0 defaults rather than
    # dividing by zero or disabling the clip.
    img, _ = _scene()
    default = background.subtract_background(img, 100, "robust_mesh")
    assert np.array_equal(
        default, background.subtract_background(img, 100, "robust_mesh", mesh_ratio=0))
    assert np.array_equal(
        default, background.subtract_background(img, 100, "robust_mesh", mesh_sigma=0))


def test_dispatch_registered():
    for m in ("robust_mesh", "fft_bandpass"):
        assert m in background.BACKGROUND_METHODS


def test_tiny_image_does_not_crash():
    # box >= image (single mesh tile) and a high-pass on a tiny frame.
    img = np.full((6, 6), 50.0, dtype=np.float32)
    img[3, 3] = 500.0
    for m in ("robust_mesh", "fft_bandpass"):
        out = background.subtract_background(img, 100, m)
        assert out.shape == img.shape
        assert np.isfinite(out).all()
