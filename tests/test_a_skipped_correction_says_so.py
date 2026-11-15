"""An empty-well correction that could not run names what was not done.

Every path below hands the frame back with a correction missing: the blank was
acquired at another binning, or it holds the nuclear channel and not the one
being corrected. The frame is then written under the usual corrected filename,
so the warning in the run log is the only thing that tells it from a corrected
one. Background ``empty_well`` on its own always warned; the flat-field paths
and the combined ``(raw − B)/(B/mean(B))`` path did not.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

import process_microscopy as pm
from util.schema import DEFAULT_SCHEMA, parse_schema
from pipeline.empty_well import _POOLED_TP

_NOT_BG = "NOT background-subtracted"
_NOT_FF = "NOT flat-field corrected"

_RAW = (500 + np.random.default_rng(1).normal(0, 5, (64, 64))).astype(np.float32)
_BLANK_BINNED = np.full((32, 32), 480, np.float32)      # the blank at 2x2 binning
_FF_BINNED = np.ones((32, 32), np.float32)
_BG_MISMATCH = {"dapi": {_POOLED_TP: _BLANK_BINNED}, "gfp": {_POOLED_TP: _BLANK_BINNED}}
_FF_MISMATCH = {"dapi": {_POOLED_TP: _FF_BINNED}, "gfp": {_POOLED_TP: _FF_BINNED}}
_BG_DAPI_ONLY = {"dapi": {_POOLED_TP: np.full((64, 64), 480, np.float32)}}
_FF_DAPI_ONLY = {"dapi": {_POOLED_TP: np.ones((64, 64), np.float32)}}


def _correct_gfp(flatfield_mode, background_method, ff_models, bg_models):
    """The GFP frame as ``_correct_image_group`` — the pipeline's own caller — returns it."""
    schema = parse_schema(DEFAULT_SCHEMA)
    _, _, _, fluor_corrected, _ = pm._correct_image_group(
        _RAW.copy(), [_RAW.copy()], nuclear_path=Path("exp_DAPI_A01_f1_t0.tif"),
        nuclear_token="DAPI", fluor_tokens=["GFP"], tophat_nir=True, tophat_radius_nir=50,
        tophat_fluor=[True], tophat_radius_fluor=[50], fft_small_nir=0, fft_small_fluor=[0],
        flatfield_mode=flatfield_mode, flatfield_models=ff_models,
        background_method=background_method, background_models=bg_models,
        smfish_tokens=[], mesh_ratio=3.0, mesh_sigma=3.0, schema=schema, sep="_")
    return fluor_corrected[0]


def _warnings(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


@pytest.mark.parametrize(
    ("label", "ff_mode", "bg_method", "ff_models", "bg_models", "expected"),
    [
        ("background alone, blank at another binning",
         "none", "empty_well", {}, _BG_MISMATCH, [_NOT_BG, "(32, 32)", "(64, 64)"]),
        ("combined, blank at another binning",
         "empty_well", "empty_well", _FF_MISMATCH, _BG_MISMATCH,
         [_NOT_BG, _NOT_FF, "(32, 32)", "(64, 64)"]),
        ("combined, blank has no GFP frames",
         "empty_well", "empty_well", _FF_DAPI_ONLY, _BG_DAPI_ONLY,
         [_NOT_BG, _NOT_FF, "no background field", "no field was built"]),
        ("flat-field alone, blank at another binning",
         "empty_well", "none", _FF_MISMATCH, {}, [_NOT_FF, "(32, 32)", "(64, 64)"]),
        ("flat-field alone, blank has no GFP frames",
         "empty_well", "none", _FF_DAPI_ONLY, {}, [_NOT_FF, "no field was built"]),
    ],
)
def test_a_frame_handed_back_uncorrected_is_warned_about(
        caplog, label, ff_mode, bg_method, ff_models, bg_models, expected):
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        out = _correct_gfp(ff_mode, bg_method, ff_models, bg_models)
    # The premise: nothing was corrected, the frame is the raw one.
    np.testing.assert_array_equal(out, _RAW, err_msg=label)
    said = _warnings(caplog)
    missing = [phrase for phrase in expected if phrase not in said]
    assert missing == [], f"{label}: the run log does not say {missing}; it says {said!r}"


def test_a_correction_that_ran_warns_about_nothing(caplog):
    """The control: with fields of the frame's shape for both channels, no warning."""
    ff = {"dapi": {_POOLED_TP: np.ones((64, 64), np.float32)},
          "gfp": {_POOLED_TP: np.ones((64, 64), np.float32)}}
    bg = {"dapi": {_POOLED_TP: np.full((64, 64), 480, np.float32)},
          "gfp": {_POOLED_TP: np.full((64, 64), 480, np.float32)}}
    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        out = _correct_gfp("empty_well", "empty_well", ff, bg)
    assert not np.array_equal(out, _RAW)
    assert _warnings(caplog) == ""


def test_no_flat_field_asked_for_is_not_a_warning(caplog):
    """``apply_flatfield(img, None)`` is Review's "no field for this mode" and stays quiet."""
    from pipeline.flatfield import apply_flatfield

    with caplog.at_level(logging.WARNING, logger="process_microscopy"):
        assert apply_flatfield(_RAW, None) is _RAW
    assert _warnings(caplog) == ""
