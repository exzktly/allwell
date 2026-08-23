"""Background corrections that return the frame they were given.

Three returns in ``pipeline.background`` are the identity transform wearing a
correction's name (audit C13). Each is a legitimate outcome of an input the
function cannot work with, and each used to be indistinguishable, in the output
and in the log, from a correction that worked:

* ``fft_bandpass`` with ``radius <= 1`` builds an all-ones transfer function, so
  the inverse FFT hands back the input — a cliff between radius 1 and radius 2.
* ``robust_mesh`` zeroes the whole mesh when not one tile yields a finite level,
  and subtracting zero is the identity.
* ``fov_median`` substitutes a level of ``0.0`` for a frame with no finite pixel,
  which is CLAUDE.md §7's cardinal conflation — "computed as zero" written the
  same way as "never computed".

The fix is not to make them do something else; there is nothing else to do. It
is to make the shortfall reach the run log, so a plate quantified from
uncorrected frames is not silently reported as a corrected one.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from pipeline.background import subtract_background

LOGGER = "process_microscopy"


def _frame(seed=0, shape=(64, 64)):
    return np.random.default_rng(seed).normal(500, 30, shape).astype(np.float32)


@pytest.mark.parametrize("radius", [0, 1])
def test_an_fft_bandpass_with_no_usable_cutoff_says_it_did_nothing(caplog, radius):
    img = _frame()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = subtract_background(img, radius, method="fft_bandpass")
    assert out == pytest.approx(np.clip(img, 0.0, None), abs=1e-2), (
        "radius <= 1 is the identity; if that changes, this test is the wrong shape"
    )
    assert "fft_bandpass" in caplog.text
    assert "UNCHANGED" in caplog.text


def test_a_real_fft_bandpass_says_nothing(caplog):
    img = _frame()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = subtract_background(img, 8, method="fft_bandpass")
    assert not np.allclose(out, np.clip(img, 0.0, None), atol=1e-2)
    assert caplog.text == ""


def test_a_mesh_that_could_not_be_estimated_says_so(caplog):
    img = np.full((32, 32), np.nan, np.float32)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = subtract_background(img, 24, method="robust_mesh")
    assert np.all(out == 0.0)
    assert "robust_mesh" in caplog.text
    assert "NOT background-subtracted" in caplog.text


def test_a_real_mesh_says_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        subtract_background(_frame(), 24, method="robust_mesh")
    assert caplog.text == ""


def test_fov_median_with_no_finite_pixel_says_so(caplog):
    img = np.full((8, 8), np.inf, np.float32)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = subtract_background(img, 5, method="fov_median")
    assert np.all(out == 0.0)
    assert "fov_median" in caplog.text
    assert "no background level could be measured" in caplog.text


def test_fov_median_on_a_real_frame_says_nothing(caplog):
    img = _frame()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = subtract_background(img, 5, method="fov_median")
    assert caplog.text == ""
    assert float(np.median(out)) == pytest.approx(0.0, abs=1.0)


def test_a_frame_whose_median_really_is_zero_is_not_a_failure(caplog):
    """Zero is a measurement. Only *unmeasurable* is a warning."""
    img = np.zeros((8, 8), np.float32)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = subtract_background(img, 5, method="fov_median")
    assert np.all(out == 0.0)
    assert caplog.text == ""


def test_the_tophat_docstring_names_the_operation_it_performs():
    """It said ``grey_closing`` and called ``grey_opening``; the code was right."""
    from pipeline.background import apply_tophat

    doc = apply_tophat.__doc__ or ""
    assert "grey_opening" in doc
    assert "grey_closing" not in doc
