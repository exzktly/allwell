"""Unit tests for microscopy_background.py (extracted from process_microscopy in P4).

These exercise the module *directly* (not via the ``process_microscopy``
re-export) so the extracted correction math is covered as a standalone unit.
Pure numpy/scipy — no ML stack required.
"""

import numpy as np
import pytest

import microscopy_background as mb


def _rng(seed=0):
    return np.random.default_rng(seed)


# ── apply_tophat ─────────────────────────────────────────────────────────────

def test_apply_tophat_removes_flat_background_and_is_nonnegative():
    img = np.full((64, 64), 200.0, dtype=np.float32)
    img[30:34, 30:34] += 500.0  # a small bright object
    out = mb.apply_tophat(img, radius=10)
    assert out.shape == img.shape
    assert out.min() >= 0.0
    # Flat background collapses toward 0; the object survives.
    assert out[0, 0] < 1.0
    assert out[31, 31] > 100.0


# ── subtract_background dispatch ─────────────────────────────────────────────

def test_subtract_background_none_is_identity():
    img = (_rng().random((32, 32)) * 100).astype(np.float32)
    out = mb.subtract_background(img, 5, "none")
    np.testing.assert_array_equal(out, img)


@pytest.mark.parametrize("method", [m for m in mb.BACKGROUND_METHODS if m not in ("none", "empty_well")])
def test_subtract_background_methods_floor_at_zero(method):
    img = (_rng(1).random((48, 48)) * 300 + 50).astype(np.float32)
    out = mb.subtract_background(img, 8, method)
    assert out.shape == img.shape
    assert np.isfinite(out).all()
    assert out.min() >= 0.0


def test_subtract_background_empty_well_uses_field():
    img = np.full((32, 32), 300.0, dtype=np.float32)
    bg = np.full((32, 32), 100.0, dtype=np.float32)
    out = mb.subtract_background(img, 0, "empty_well", bg)
    assert out.shape == img.shape
    assert out.min() >= 0.0
    # A constant field at a lower level should pull the flat image down.
    assert out.mean() < img.mean()


def test_subtract_background_empty_well_without_field_is_identity():
    img = (_rng(2).random((16, 16)) * 100).astype(np.float32)
    out = mb.subtract_background(img, 0, "empty_well", None)
    np.testing.assert_array_equal(out, img)


# ── estimate_background_field ────────────────────────────────────────────────

def test_estimate_background_field_keeps_level_and_smooths():
    stack = [np.full((24, 24), 120.0, dtype=np.float32) for _ in range(3)]
    field = mb.estimate_background_field(stack, sigma=2.0)
    assert field is not None
    assert field.shape == (24, 24)
    # Median of constant frames keeps the measured level (not mean-1 normalized).
    assert field.mean() == pytest.approx(120.0, rel=1e-3)


def test_estimate_background_field_returns_none_on_empty_or_mismatched():
    assert mb.estimate_background_field([]) is None
    assert mb.estimate_background_field([np.zeros((4, 5)), np.zeros((6, 7))]) is not None  # first shape wins
    # All-mismatched-against-first beyond the first frame are dropped, but the
    # first is kept; a stack of differently-shaped-from-each-other 1-D arrays is None.
    assert mb.estimate_background_field([np.zeros((3,)), np.zeros((4,))]) is None


# ── _match_background_scale ──────────────────────────────────────────────────

def test_match_background_scale_aligns_level():
    rng = _rng(3)
    bg = (rng.random((40, 40)) * 50 + 100).astype(np.float32)
    # Image whose background is the field scaled by 2 plus offset.
    img = (2.0 * bg + 10.0).astype(np.float32)
    fitted = mb._match_background_scale(img, bg)
    assert fitted.shape == bg.shape
    # The fitted field should track the image's background scale (≈2·bg+10),
    # i.e. be much closer to img than the raw bg is.
    assert np.mean(np.abs(fitted - img)) < np.mean(np.abs(bg - img))


# ── combined_empty_well_correct ──────────────────────────────────────────────

def test_combined_empty_well_recovers_signal():
    # raw = gain*signal + B with gain ∝ B (illumination tracks background).
    rng = _rng(4)
    base = (rng.random((40, 40)) * 40 + 80).astype(np.float32)  # the empty-well B
    ff = (base / base.mean()).astype(np.float32)                # mean-1 illumination
    signal = np.zeros((40, 40), dtype=np.float32)
    signal[10:15, 10:15] = 50.0
    raw = (ff * signal + base).astype(np.float32)
    out = mb.combined_empty_well_correct(raw, base, ff)
    assert out.shape == raw.shape
    assert np.isfinite(out).all()
    # The empty region maps near 0; the signal region is clearly above it.
    assert out[0, 0] < out[12, 12]


def test_combined_empty_well_noops_when_fields_absent():
    img = (_rng(5).random((20, 20)) * 100).astype(np.float32)
    np.testing.assert_array_equal(mb.combined_empty_well_correct(img, None, None), img)


# ── _floor_after_subtract ────────────────────────────────────────────────────

def test_floor_after_subtract_clips_negatives():
    diff = np.array([[-5.0, 0.0], [3.0, -1.0]], dtype=np.float32)
    out = mb._floor_after_subtract(diff)
    assert out.min() >= 0.0
    assert out[1, 0] == pytest.approx(3.0)


# ── robust_mesh tuning is honoured ───────────────────────────────────────────

def test_robust_mesh_params_change_output():
    # radius must be large enough that the two ratios land on different tile
    # sizes: box = max(8, round(radius/ratio)). At radius=60 that's 20 vs 10.
    img = (_rng(6).random((96, 96)) * 300 + 50).astype(np.float32)
    a = mb.subtract_background(img, 60, "robust_mesh", mesh_ratio=3.0)
    b = mb.subtract_background(img, 60, "robust_mesh", mesh_ratio=6.0)
    assert not np.allclose(a, b)
