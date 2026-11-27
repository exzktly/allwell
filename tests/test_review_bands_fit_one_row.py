"""The bands fitted to one row by the user's decisions (tracker §9.1 steps 15–19) are one row at the desk's width,
measured through the registry's own builder (design language §6).

UI lane (``tests/_lanes.py::UI_LANE_FILES``): a width in the drawn font, which CI's runner draws in a wider fallback
than the app ships, so it runs locally only. Where the moved controls went is behaviour and is held in CI by
``tests/test_review_bands_are_one_row.py``.
"""
from __future__ import annotations

import pytest

from tests.test_review_bands_are_one_row import WIDTHS, _one_row


@pytest.mark.parametrize("name", ["Statistics", "Spatial", "Multivariate", "Segmentation"])
def test_the_band_is_one_row_at_the_desk(qtbot, mini_dataset, name):
    _ctx, _window, _bands, found = _one_row(qtbot, name, mini_dataset)
    assert found == {w: [] for w in WIDTHS}
