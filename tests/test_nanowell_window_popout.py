"""The Distribution window in the pop-out: its rows, pages, clicks and controls — and the bounded store of crops it
pages through.

Asked for: the window's nanowells shown in the pop-out as rows of crops over time. A window can hold hundreds of
nanowells and ``st.crops`` was never pruned, so the store keeps a bounded number and never lets go of the nanowell
selected or the page on screen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.crop_cache_compute import CropCache  # noqa: E402
from review.features.nanowell.state import NanowellState  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401


def test_the_crop_store_keeps_its_bound():
    cache = CropCache(maxlen=3)
    cache.protected = {"a"}
    cache["a"] = 1
    cache.update({"b": 2, "c": 3, "d": 4, "e": 5})
    assert list(cache) == ["a", "d", "e"]               # the oldest unprotected go; "a" is the oldest and stays


def test_every_place_that_empties_the_store_keeps_it_bounded(page, qtbot):  # noqa: F811
    assert isinstance(NanowellState().crops, CropCache)
    fresh = NanowellState()
    fresh.crops["x"] = 1
    fresh.reset_data()
    assert isinstance(fresh.crops, CropCache) and not fresh.crops
    st = _loaded(qtbot, page)
    nwc.set_image_channel(page, "GFP")
    assert isinstance(st.crops, CropCache)
