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


def test_binary_modes_via_display_menu_radio(loaded_app):
    app = loaded_app
    C.image_table_apply_preset(app, "channel", "timepoint")
    # The Display ▾ menu's Binary radio group (None / Mask / ≥ Threshold) is
    # exclusive and sets the mode absolutely.
    C.image_table_set_binary_mode(app, "mask")
    assert app._image_table_binary_mode == "mask"
    assert app._image_table_binary_act_mask.isChecked()
    assert not app._image_table_binary_act_thresh.isChecked()
    assert not app._image_table_binary_act_none.isChecked()

    C.image_table_set_binary_mode(app, "threshold")
    assert app._image_table_binary_mode == "threshold"
    assert app._image_table_binary_act_thresh.isChecked()
    assert not app._image_table_binary_act_mask.isChecked()

    C.image_table_set_binary_mode(app, "")  # None turns binary off
    assert app._image_table_binary_mode == ""
    assert app._image_table_binary_act_none.isChecked()
    assert not app._image_table_binary_act_thresh.isChecked()


def test_display_menu_toggles_flip_state(loaded_app):
    app = loaded_app
    t0 = bool(app._image_table_use_tophat)
    C.image_table_toggle_tophat(app)
    assert app._image_table_use_tophat is (not t0)
    assert app._image_table_tophat_act.isChecked() is (not t0)
    b0 = bool(app._image_table_show_boundaries)
    C.image_table_toggle_boundaries(app)
    assert app._image_table_show_boundaries is (not b0)
    assert app._image_table_boundaries_act.isChecked() is (not b0)


def _row_values(app, dim):
    key = {"channel": "chan_cb", "timepoint": "tp_cb",
           "fov": "fov_cb", "well": "well_cb"}[dim]
    return [[cell[key].currentText().strip() for cell in row]
            for row in app._image_table_cells]


def test_add_row_uniform_clones_and_varies_channel(loaded_app):
    app = loaded_app
    C.image_table_repopulate_dropdowns(app)
    C.image_table_apply_preset(app, "channel", "timepoint")
    n0 = len(app._image_table_cells)
    cols0 = len(app._image_table_cells[0])
    tps_before = _row_values(app, "timepoint")

    # Add a row cloned from row 1, uniformly set to a chosen channel.
    chans = C._channel_options(app)
    target = chans[-1]
    C.image_table_add_varied_row(
        app, src_idx=0, dim="channel", mode="uniform", value=target)

    assert len(app._image_table_cells) == n0 + 1
    new_row = app._image_table_cells[-1]
    # whole new row carries the chosen channel…
    assert all(cell["chan_cb"].currentText().strip() == target for cell in new_row)
    # …but the other dimension (timepoints) is copied from the source row.
    assert _row_values(app, "timepoint")[-1] == tps_before[0]
    assert len(new_row) == cols0


def test_add_row_step_advances_each_cell(loaded_app):
    app = loaded_app
    C.image_table_repopulate_dropdowns(app)
    # rows = wells, cols = timepoints so the source row has a single channel
    C.image_table_apply_preset(app, "well", "timepoint")
    tp_opts = C._timepoint_options(app)
    if len(tp_opts) < 2:
        import pytest as _pytest
        _pytest.skip("need ≥2 timepoints to test Step mode")
    src_tps = _row_values(app, "timepoint")[0]

    C.image_table_add_varied_row(
        app, src_idx=0, dim="timepoint", mode="step", value="")

    new_tps = _row_values(app, "timepoint")[-1]
    # each cell advanced one timepoint (clamped at the last value)
    for src, new in zip(src_tps, new_tps):
        i = tp_opts.index(src)
        assert new == tp_opts[min(i + 1, len(tp_opts) - 1)]


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
