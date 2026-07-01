"""Threshold quick-edit on Segmentation + Image Table tabs edits ThreshFracOn."""
from __future__ import annotations
import pytest
pytest.importorskip("PySide6")
from well_viewer.features.image_browser.controller import show_image_browser_subtab


def test_segmentation_threshold_stepper_edits_thresh_frac(loaded_app):
    app = loaded_app
    assert show_image_browser_subtab(app, "Segmentation")
    st = getattr(app, "_review_thresh_frac_stepper", None)
    assert st is not None, "Segmentation tab should host an ON-threshold stepper"
    ch = app._app_state.channel.active_channel
    st.setValue(123.0)  # emits valueChanged -> set_review_thresh_frac
    assert abs(app._get_thresh_frac_on(ch) - 123.0) < 1e-6


def test_image_table_threshold_stepper_edits_thresh_frac(loaded_app):
    app = loaded_app
    assert show_image_browser_subtab(app, "Image Table")
    st = getattr(app, "_image_table_thresh_frac_stepper", None)
    assert st is not None, "Image Table tab should host an ON-threshold stepper"
    ch = app._app_state.channel.active_channel
    st.setValue(77.0)
    assert abs(app._get_thresh_frac_on(ch) - 77.0) < 1e-6


def test_quick_edit_steppers_sync_on_refresh(loaded_app):
    app = loaded_app
    show_image_browser_subtab(app, "Segmentation")
    show_image_browser_subtab(app, "Image Table")
    # Set via the canonical setter, then the shared refresh should sync both.
    app._set_thresh_frac_on(55.0)
    app._refresh_thresh_frac_quick_edit()
    assert abs(app._review_thresh_frac_stepper.value() - 55.0) < 1e-6
    assert abs(app._image_table_thresh_frac_stepper.value() - 55.0) < 1e-6
