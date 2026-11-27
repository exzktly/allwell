"""Review-image (Segmentation tab) LUT min/max should take effect, including
when only one box is edited and the other is left at 'auto'.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
import numpy as np
from PySide6.QtWidgets import QLineEdit

from review.features.image_browser.review_image.renderer import review_image_resolve_lut


def _ctx(min_text, max_text):
    # A8 D4: review_image_resolve_lut is ctx-native — the LUT boxes are the
    # (unregistered) Qt widgets it reaches via ctx.window; active channel + the
    # per-channel LUT cache are typed ctx.state.
    return SimpleNamespace(
        window=SimpleNamespace(
            _review_lmin_entry=QLineEdit(min_text),
            _review_lmax_entry=QLineEdit(max_text),
        ),
        state=SimpleNamespace(
            channel=SimpleNamespace(active_image_channel="gfp"),
            image_view=SimpleNamespace(lut_by_channel={}),
        ),
    )


ARR = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)  # min 0, max 30


def test_both_numeric_used(qtbot):
    lo, hi = review_image_resolve_lut(_ctx("8", "22"), ARR)
    assert (lo, hi) == (8.0, 22.0)


def test_both_auto_falls_back_to_data_range(qtbot):
    lo, hi = review_image_resolve_lut(_ctx("auto", "auto"), ARR)
    assert (lo, hi) == (0.0, 30.0)


def test_only_min_edited_other_auto_still_applies(qtbot):
    # max="auto" must not discard the typed min — fill max from the data range.
    lo, hi = review_image_resolve_lut(_ctx("5", "auto"), ARR)
    assert (lo, hi) == (5.0, 30.0)


def test_only_max_edited_other_blank_still_applies(qtbot):
    lo, hi = review_image_resolve_lut(_ctx("", "25"), ARR)
    assert (lo, hi) == (0.0, 25.0)


def test_resolved_value_is_cached_per_channel(qtbot):
    ctx = _ctx("3", "27")
    review_image_resolve_lut(ctx, ARR)
    assert ctx.state.image_view.lut_by_channel["gfp"] == (3.0, 27.0)


def test_segmentation_brightness_is_the_range_page_wired_to_commit_and_auto(loaded_app, monkeypatch):
    """Segmentation's Brightness page is the one ``RangePage``. A typed range
    commits it; Auto fits the range to the image, as the old Auto LUT did."""
    from review.features.image_browser.controller import show_image_browser_subtab
    from review.features.preview import panel_view as PV
    from widgets.range_page import RangePage

    ctx = loaded_app._ctx
    assert show_image_browser_subtab(ctx, "Segmentation")
    calls = []
    monkeypatch.setattr(PV, "review_image_commit_lut", lambda c: calls.append("commit"))
    monkeypatch.setattr(PV, "review_image_auto_lut", lambda c: calls.append("auto"))
    page = loaded_app.findChild(RangePage, "ReviewImageRange")
    assert page is not None
    assert ctx.widgets.get("_review_lmin_entry") is page.low_edit
    assert ctx.widgets.get("_review_lmax_entry") is page.high_edit
    page.low_edit.setText("5")
    page.high_edit.setText("90")
    page.high_edit.editingFinished.emit()
    page.auto_button.click()
    assert calls == ["commit", "auto"]
