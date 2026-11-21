"""A cell reads the same whether the field around it is sparse or crowded.

The full-app review (pull request #1343), rows C1 and C2. Identical synthetic cells of a
known mean over a known background, at increasing cover:

* ``empty_well`` fitted the blank to the dimmest 60% of the frame, which holds cell pixels once cells cover
  more than 40% of it, so the fitted background rose into the signal: true mean 500 read 418 at 52% cover.
* ``fft_bandpass`` floored a zero-*mean* frame at zero, so the background sat below zero by an amount that
  grew with the number of cells: 493 with 20 cells in the field, 337 with 3,000.
"""
from __future__ import annotations

import numpy as np
import pytest

from pipeline.background import _match_background_scale, subtract_background

SIZE, CELL, TRUE_MEAN = 512, 7, 500.0


def _field(cover: float, blank: np.ndarray, seed: int = 0):
    """Square cells of side ``CELL`` on a jittered grid covering about *cover* of the frame."""
    rng = np.random.default_rng(seed)
    cells = np.zeros((SIZE, SIZE), bool)
    pitch = max(CELL + 1, int(round(CELL / np.sqrt(cover))))
    for y in range(2, SIZE - CELL - 2, pitch):
        for x in range(2, SIZE - CELL - 2, pitch):
            cells[y:y + CELL, x:x + CELL] = True
    image = blank + TRUE_MEAN * cells + rng.normal(0.0, 5.0, blank.shape)
    return image.astype(np.float32), cells


def _vignetted() -> np.ndarray:
    yy, xx = np.mgrid[:SIZE, :SIZE]
    r = np.hypot(yy - SIZE / 2, xx - SIZE / 2) / (SIZE / np.sqrt(2))
    return (600.0 - 300.0 * r).astype(np.float32)


@pytest.mark.parametrize("cover", [0.02, 0.25, 0.45, 0.55, 0.70, 0.78])
@pytest.mark.parametrize("blank", ["vignetted", "flat"])
def test_the_empty_well_fit_ignores_the_cells(cover, blank):
    field = _vignetted() if blank == "vignetted" else np.full((SIZE, SIZE), 500.0, np.float32)
    image, cells = _field(cover, field)
    assert abs(cells.mean() - cover) < 0.08, "the fixture covers what it says"

    fitted = _match_background_scale(image, field)
    corrected = subtract_background(image, 50, "empty_well", field)

    assert float(np.abs(fitted - field).max()) < 2.0          # the background, not the background plus signal
    assert float(corrected[cells].mean()) == pytest.approx(TRUE_MEAN, abs=2.0)


def test_the_empty_well_fit_still_absorbs_an_exposure_mismatch_and_a_pedestal():
    """What the fit exists for: the sample acquired at 1.3x the blank's exposure, on a 40-count pedestal."""
    blank = _vignetted()
    image, cells = _field(0.5, 1.3 * blank + 40.0)
    corrected = subtract_background(image, 50, "empty_well", blank)
    assert float(corrected[cells].mean()) == pytest.approx(TRUE_MEAN, abs=2.0)
    assert float(corrected[~cells].mean()) < 5.0


def test_without_cells_the_fit_is_the_blank():
    blank = _vignetted()
    image = (blank + np.random.default_rng(1).normal(0.0, 5.0, blank.shape)).astype(np.float32)
    assert float(np.abs(_match_background_scale(image, blank) - blank).max()) < 1.0


def test_fft_bandpass_does_not_depend_on_how_many_cells_share_the_field():
    """Radius well above the cell size, so the filter passes the cells; what is left is the reference level."""
    flat = np.full((SIZE, SIZE), 300.0, np.float32)
    means = []
    for cover in (0.01, 0.10, 0.35):
        image, cells = _field(cover, flat)
        means.append(float(subtract_background(image, 200, "fft_bandpass")[cells].mean()))
    assert max(means) - min(means) < 0.03 * TRUE_MEAN, means     # was 493 -> 337, a third of the signal
    assert min(means) > 0.93 * TRUE_MEAN, means


@pytest.mark.parametrize("cover", [0.04, 0.48, 0.56, 0.70])
def test_fft_bandpass_reads_the_cells_on_either_side_of_half_cover(cover):
    """The re-reference level is the background's, not the frame's median.

    Past half cover the median of the filtered frame is a cell pixel, and taking
    it as the zero subtracted the cells from themselves: identical cells of mean
    500 read about 35 at 56% cover. The level is now taken from the frame's low
    tail (``_background_tail_level``), which is background up to 80% cover.
    """
    flat = np.full((SIZE, SIZE), 300.0, np.float32)
    image, cells = _field(cover, flat)
    assert abs(cells.mean() - cover) < 0.06, "the fixture covers what it says"
    corrected = subtract_background(image, 200, "fft_bandpass")
    assert float(corrected[cells].mean()) == pytest.approx(TRUE_MEAN, rel=0.03)
