"""A8 burn-down — image_table + cell_browser widget handles into the registry.

Both Image Browser sub-tab builders held their Qt widgets in locals and then
stashed them on the app god-object. They now register through ``ctx.widgets``
(which stamps ``app._<name>``) so the controllers' legacy readers keep resolving.
State lists/dicts, the crop tool, and the cell-image loader / refreshers stay on
the app (a later layer).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.image_browser.image_table.tab_view import build_image_table_tab
from well_viewer.features.image_browser.cell_browser.tab_view import build_cell_browser_tab

# The builder shelf owns authoring; the render model is headless plain data, so
# the old per-cell selector grid / per-row LUT table / grid-size + held-value
# combos are gone (not registered). What remains are the action-strip + render
# handles the controller / persistence still read.
_IMAGE_TABLE = (
    "_image_table_props_dock", "_image_table_crop_act",
    "_image_table_thresh_chan_cb", "_image_table_thresh_frac_stepper",
    "_image_table_render_grid", "_image_table_zoom_lbl",
    "_image_table_empty_state",
)

_CELL_BROWSER = (
    "_cell_browser_channel_host", "_cell_browser_lut_btn", "_cell_browser_lut_panel",
    "_cell_browser_lut_layout", "_cell_browser_boundary_cb", "_cell_browser_label_spot_cb",
    "_cell_browser_source_seg", "_cell_browser_pagesize_edit", "_cell_browser_load_more_btn",
    "_cell_browser_generate_btn", "_cell_browser_count_label", "_cell_browser_grid",
    "_cell_browser_grid_host",
)


def _check(app, names):
    for name in names:
        assert app._widget_registry.has(name), f"{name} not registered"
        assert getattr(app, name, None) is not None, f"{name} not on app"
        assert getattr(app, name) is app._widget_registry.get(name)


def test_image_table_build_registers_handles(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_image_table_tab(app._ctx, parent)
    _check(app, _IMAGE_TABLE)


def test_cell_browser_build_registers_handles(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_cell_browser_tab(app._ctx, parent)
    _check(app, _CELL_BROWSER)
