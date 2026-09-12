"""Live (headless Qt) checks for the Image Table tab: the plain-data render
model builds without rendering, and zoom updates its label.

The old channel dropdown, per-row LUT/Display boxes, and Add-row/step controls
were retired with the drag-and-drop builder shelf; per-channel display now lives
on the shelf's ⚙ (``channel_opts``) and per-cell tweaks on the right-click
override popover.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.image_browser.image_table import controller as C


def test_build_does_not_render(image_table_app):
    app = image_table_app
    app._app_state.image_table.last_render = {}
    # rows=channels, cols=timepoints
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    # The plain-data render model was assembled…
    cells = app._ctx.runtime.image_table.cells
    assert cells, "Build should assemble the render model"
    assert all(isinstance(cell, dict) for row in cells for cell in row)
    # …but no images were rendered (Build must not auto-generate).
    assert not app._app_state.image_table.last_render


def test_zoom_updates_label(image_table_app):
    app = image_table_app
    C.image_table_set_zoom(app._ctx, 1.5)
    assert abs(app._app_state.image_table.zoom - 1.5) < 1e-9
    assert app._image_table_zoom_lbl.text() == "150%"
