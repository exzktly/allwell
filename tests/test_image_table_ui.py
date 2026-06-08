"""Live (headless Qt) checks for the Image Table tab controls overhaul:
channel dropdown, binary modes, zoom, and Build-without-render.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_table import controller as C  # noqa: E402


def test_channel_dropdown_populates_and_filters(loaded_app):
    app = loaded_app
    # Activating the tab repopulates the dropdowns (same as the global combos).
    C.image_table_repopulate_dropdowns(app)
    # The layout-row channel dropdown menu exists and is populated.
    menu = getattr(app, "_image_table_channel_menu", None)
    assert menu is not None
    checkable = [a for a in menu.actions() if a.isCheckable()]
    opts = C._channel_options(app)
    assert {a.text() for a in checkable} == set(opts)
    # Toggling channels off the dropdown filters the Channels axis.
    app._image_table_active_channels = set()
    C.image_table_toggle_channel(app, opts[0], True)
    assert C._axis_values(app, "channel") == [opts[0]]


def test_build_does_not_render(loaded_app):
    app = app = loaded_app
    app._image_table_last_render = {}
    # rows=channels, cols=timepoints
    C.image_table_apply_preset(app, "channel", "timepoint")
    # Selector grid was built…
    assert app._image_table_cells, "Build should assemble the selector grid"
    # …but no images were rendered (Build must not auto-generate).
    assert not app._image_table_last_render


def test_binary_modes_mutually_exclusive(loaded_app):
    app = loaded_app
    C.image_table_apply_preset(app, "channel", "timepoint")
    C.image_table_set_binary_mode(app, "mask")
    assert app._image_table_binary_mode == "mask"
    assert app._image_table_binary_mask_btn.isChecked()
    assert not app._image_table_binary_thresh_btn.isChecked()
    # switching to threshold turns mask off
    C.image_table_set_binary_mode(app, "threshold")
    assert app._image_table_binary_mode == "threshold"
    assert not app._image_table_binary_mask_btn.isChecked()
    assert app._image_table_binary_thresh_btn.isChecked()
    # clicking the active one again turns binary off
    C.image_table_set_binary_mode(app, "threshold")
    assert app._image_table_binary_mode == ""
    assert not app._image_table_binary_thresh_btn.isChecked()


def test_zoom_updates_label(loaded_app):
    app = loaded_app
    C.image_table_set_zoom(app, 1.5)
    assert abs(app._image_table_zoom - 1.5) < 1e-9
    assert app._image_table_zoom_lbl.text() == "150%"


def test_lut_boxes_autofill_real_numbers_from_render(loaded_app):
    import numpy as np
    app = loaded_app
    C.image_table_repopulate_dropdowns(app)
    C.image_table_apply_preset(app, "channel", "timepoint")
    cells = app._image_table_cells
    assert cells
    # Synthesize a rendered array per cell (real image discovery is dataset-
    # specific); the autofill must pool these into the LUT boxes as numbers.
    rendered = {}
    for r, row in enumerate(cells):
        for c, _cell in enumerate(row):
            rendered[(r, c)] = (np.arange(16, dtype=np.float32).reshape(4, 4) + r)
    C._refresh_lut_fields_from_render(app, rendered)

    luts = app._image_table_lut or {}
    filled = []
    for chan, entry in luts.items():
        txt = entry["min"].text().strip()
        if txt and txt.lower() != "auto":
            float(txt)  # parses as a real number, not "auto"
            filled.append(chan)
    assert filled, "LUT boxes should auto-fill with real numbers, not 'auto'"

    # A user override (auto=False) must NOT be clobbered by a later refresh.
    chan0 = next(iter(luts))
    luts[chan0]["auto"] = False
    luts[chan0]["min"].setText("999")
    C._refresh_lut_fields_from_render(app, rendered)
    assert luts[chan0]["min"].text() == "999"
