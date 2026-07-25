"""Segmentation-tab flat-field validation view: the Raw / Flat-field / Corrected
source control, the on-the-fly flat-field recompute, and source switching.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.review_image import controller as C
from well_viewer.features.image_browser.review_image.renderer import set_review_image_source


def _set_flatfield_mode(app, mode, background_well=""):
    info = dict(app._app_state.dataset.pipeline_info or {})
    info["qc_options"] = {"flatfield": mode, "background_well": background_well}
    app._app_state.dataset.pipeline_info = info


def _build_segmentation_panel(app):
    """Build the Segmentation control strip, which owns the Stage control.

    Tab bodies build lazily, so ``loaded_app`` alone leaves
    ``ctx.runtime.review_image.source_seg`` at its ``None`` default — the
    builder in ``preview/panel_view.build_review_image_panel`` is what assigns
    it. Every other Image Browser sub-tab test does the same thing.
    """
    from well_viewer.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app._ctx, "Segmentation")
    return app._ctx.runtime.review_image.source_seg


def test_source_segmented_control_built(loaded_app):
    seg = _build_segmentation_panel(loaded_app)
    assert seg is not None
    data = [seg._data[i] for i in range(seg.count())]
    assert data == ["raw", "flatfield", "corrected"]
    # Default stage is the saved final image.
    assert loaded_app._app_state.review_image.image_source == "corrected"


def test_flatfield_field_for_gaussian_and_none(loaded_app):
    app = loaded_app
    arr = (np.random.default_rng(0).random((48, 48)).astype(np.float32) * 100)
    _set_flatfield_mode(app, "gaussian")
    # A8 D4: flatfield_field_for is ctx-native — pass the app's context.
    field = C.flatfield_field_for(app._ctx, "GFP", arr)
    assert field is not None and field.shape == arr.shape
    assert abs(float(field.mean()) - 1.0) < 1e-2          # mean-1 field
    _set_flatfield_mode(app, "none")
    assert C.flatfield_field_for(app._ctx, "GFP", arr) is None  # identity → no-op


def test_set_source_updates_state_syncs_control_and_clears_lut(loaded_app):
    app = loaded_app
    _build_segmentation_panel(app)
    chan = str(app._app_state.channel.active_image_channel or "").lower()
    app._app_state.image_view.lut_by_channel[chan] = (10.0, 200.0)

    set_review_image_source(app._ctx, "flatfield")
    assert app._app_state.review_image.image_source == "flatfield"
    assert app._ctx.runtime.review_image.source_seg.currentData() == "flatfield"
    # Switching stages drops the active channel's cached LUT (ranges differ).
    assert chan not in app._app_state.image_view.lut_by_channel

    set_review_image_source(app._ctx, "corrected")
    assert app._app_state.review_image.image_source == "corrected"
    assert app._ctx.runtime.review_image.source_seg.currentData() == "corrected"

    set_review_image_source(app._ctx, "bogus")  # invalid → ignored
    assert app._app_state.review_image.image_source == "corrected"


def test_flatfield_cache_invalidated_on_dataset_caches_clear(loaded_app):
    app = loaded_app
    app._app_state.review_image.flatfield_field_cache[("gfp", "basic")] = object()
    app._app_state.review_image.clear_caches()
    assert app._app_state.review_image.flatfield_field_cache == {}
