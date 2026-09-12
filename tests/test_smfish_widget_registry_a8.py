"""A8 burn-down — smFISH widget handles into the WidgetRegistry.

The smFISH tab builder stashed its Qt widgets (channel/fov/tp combos, threshold +
LUT edits, overlay button, status label, matplotlib figure/axes/canvas/toolbar,
and the tab frame) on the app god-object amid a large block of non-widget state.
The widgets now register through ``ctx.widgets`` (which stamps ``app._smfish_*``)
so the smFISH controller's legacy readers keep resolving; the state fields stay.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from review.features.smfish.tab_view import build_smfish_tab
from tests.widget_registry_helpers import assert_handles_registered

_HANDLES = (
    "_smfish_frame", "_smfish_channel_cb", "_smfish_fov_cb", "_smfish_tp_cb",
    "_smfish_threshold_edit", "_smfish_lut_min_edit", "_smfish_lut_max_edit",
    "_smfish_overlay_btn", "_smfish_status_label", "_smfish_fig_img",
    "_smfish_ax_img", "_smfish_canvas_img", "_smfish_toolbar",
)


def test_build_registers_handles_and_stamps_app(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_smfish_tab(app._ctx, parent)

    assert_handles_registered(app, _HANDLES)
