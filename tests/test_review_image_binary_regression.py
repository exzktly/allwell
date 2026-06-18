"""Regression: the binary-mask threshold map must run its diagnostic log.

``review_build_threshold_map`` powers Image-Table / Review-Image *binary mode*
("turning binary mode on and off"). It emits one ``_logger`` diagnostic line per
build. When the function was relocated into ``review_image/collect.py`` the
module-level ``_logger`` was left behind, so the first real binary-mode render
raised ``NameError: _logger is not defined`` — a path the unit tests didn't
exercise (they early-return before the diagnostic). This drives the function all
the way through the diagnostic on a real loaded app so the regression can't
return.
"""

from __future__ import annotations

import numpy as np
import pytest

from well_viewer.features.image_browser.review_image.collect import (
    review_build_threshold_map,
    review_row_keys_series,
)

pytestmark = pytest.mark.qt


def test_binary_threshold_map_reaches_diagnostic_without_nameerror(loaded_app):
    app = loaded_app
    well = "A01"
    df = app._get_rows(well)
    keys = review_row_keys_series(app, df)
    assert keys is not None and len(keys) > 0

    # Pick a real (fov, tp) present in the data and the nucleus ids there, so the
    # function gets past its early returns and into the per-build diagnostic log.
    fov, tp, _ = keys.iloc[0]
    nids = sorted({int(k[2]) for k in keys if k[0] == fov and k[1] == tp and k[2]})
    assert nids, "expected at least one cell at the chosen FOV/timepoint"

    mask = np.array([[0, *nids]], dtype=np.int32)  # tiny label image holding those nids

    # Before the fix this raised NameError from the diagnostic _logger call.
    out = review_build_threshold_map(app, mask, well, fov, tp)
    assert isinstance(out, dict)
    assert set(out) == set(nids)
    assert all(isinstance(v, bool) for v in out.values())
