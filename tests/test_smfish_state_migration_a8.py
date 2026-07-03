"""A8 state migration — smFISH separator/show_overlays/fit_on_next_redraw.

These scalar flags used to live on the god-object as ``app._smfish_separator`` /
``_smfish_show_overlays`` / ``_smfish_fit_on_next_redraw``. They now live on the
typed ``SmfishState`` slice (``app._app_state.smfish.*``). This proves the slice
defaults, that the god-object attributes are gone, and that the overlay toggle
operates on the slice.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.smfish.tab_view import (
    _smfish_toggle_overlays,
    build_smfish_tab,
)


def test_slice_defaults(viewer_app):
    sm = viewer_app._app_state.smfish
    assert sm.separator == "_"
    assert sm.show_overlays is True
    assert sm.fit_on_next_redraw is True
    # The god-object attributes are gone.
    assert not hasattr(viewer_app, "_smfish_separator")
    assert not hasattr(viewer_app, "_smfish_show_overlays")
    assert not hasattr(viewer_app, "_smfish_fit_on_next_redraw")


def test_toggle_overlays_flips_the_slice(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_smfish_tab(app._ctx, parent)

    assert app._app_state.smfish.show_overlays is True
    _smfish_toggle_overlays(app)
    assert app._app_state.smfish.show_overlays is False
    assert app._smfish_overlay_btn.text() == "Show Overlays"
    _smfish_toggle_overlays(app)
    assert app._app_state.smfish.show_overlays is True
    assert app._smfish_overlay_btn.text() == "Hide Overlays"
