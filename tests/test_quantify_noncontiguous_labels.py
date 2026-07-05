"""Regression test for the ``invalid value encountered in divide`` warning
(scipy ``_measurements.py``) emitted during per-nucleus quantification.

The seeded-watershed segmentation method routinely produces *non-contiguous*
label ids: a nucleus marker whose watershed basin lands entirely outside the
cytoplasm mask contributes no pixels, so its id is simply absent from the label
image (a gap in 1..labels.max()). ``scipy.ndimage.standard_deviation`` computes
``sums / counts`` over the full label histogram before selecting the requested
indices, so those zero-count gap ids hit 0/0 and spam a RuntimeWarning — even
though the statistics for the ids we actually ask for are correct and finite.

``_quantify_image_group`` wraps the measurement calls in ``np.errstate`` so the
noise is silenced without relabelling (which would renumber every cell's
nucleus_id). This test asserts that quantifying an image with gapped labels
neither raises the warning nor produces non-finite intensity columns.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from process_microscopy import _quantify_image_group, parse_schema  # noqa: E402


def _gapped_label_image():
    """8x8 label image with ids {1, 4} — a gap at 2 and 3 (no such pixels)."""
    labels = np.zeros((8, 8), dtype=np.int32)
    labels[0:3, 0:3] = 1
    labels[4:7, 4:7] = 4
    return labels


def test_quantify_gapped_labels_emits_no_divide_warning():
    labels = _gapped_label_image()
    fluor = np.random.default_rng(0).random((8, 8)).astype(np.float32)
    nir = fluor.copy()

    with warnings.catch_warnings():
        # Any "invalid value encountered in divide" leaking out fails the test.
        warnings.simplefilter("error")
        records = _quantify_image_group(
            labels=labels,
            fluor_tophat=[fluor],
            fluor_tokens=["gfp"],
            nir_corr=nir,
            nuclear_path=Path("exp_nuc_A01_f01_T01.tif"),
            schema=parse_schema("experiment:channel:well:fov:timepoint"),
            sep="_",
            shape_features=[],
        )

    # One record per present label (2), and every intensity column is finite —
    # the gap ids never leak NaN into the real cells' stats.
    assert len(records) == 2
    for rec in records:
        for key, val in rec.items():
            if key.endswith("_intensity"):
                assert np.isfinite(val), f"{key} is non-finite: {val}"
