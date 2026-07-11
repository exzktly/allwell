"""Threshold quick-edit on the Segmentation tab edits ThreshFracOn."""
from __future__ import annotations
import pytest
pytest.importorskip("PySide6")
from well_viewer.features.image_browser.controller import show_image_browser_subtab


def test_segmentation_threshold_stepper_edits_thresh_frac(loaded_app):
    app = loaded_app
    assert show_image_browser_subtab(app._ctx, "Segmentation")
    st = getattr(app, "_review_thresh_frac_stepper", None)
    assert st is not None, "Segmentation tab should host an ON-threshold stepper"
    ch = app._app_state.channel.active_channel
    st.setValue(123.0)  # emits valueChanged -> set_review_thresh_frac
    assert abs(app._get_thresh_frac_on(ch) - 123.0) < 1e-6


def test_quick_edit_stepper_syncs_on_refresh(loaded_app):
    app = loaded_app
    show_image_browser_subtab(app._ctx, "Segmentation")
    # Set via the canonical setter, then the shared refresh should sync the
    # Segmentation stepper (and the Plotting ctxbar stepper).
    app._set_thresh_frac_on(55.0)
    app._refresh_thresh_frac_quick_edit()
    assert abs(app._review_thresh_frac_stepper.value() - 55.0) < 1e-6
