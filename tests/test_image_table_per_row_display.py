"""Image Table: per-row LUT + display (top-hat / boundaries / binary).

The LUT/Display table is keyed per grid ROW (not deduped per channel), so two
rows showing the same channel are independent, and each row carries its own
top-hat / boundaries / binary controls which the render + export read per-row.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt

from well_viewer.features.image_table import controller as C
from well_viewer.features.image_table.image_compose import _parse_lut, _row_display_opts


def test_lut_table_is_per_row_not_per_channel(loaded_app):
    app = loaded_app
    C.image_table_repopulate_dropdowns(app)
    # rows = wells, cols = timepoints -> each row is the same single channel,
    # so a per-channel table would collapse to one box; per-row keeps one each.
    C.image_table_apply_preset(app, "well", "timepoint")
    cells = app._image_table_cells
    luts = app._image_table_lut
    assert set(luts) == set(range(len(cells))), "one LUT/Display box per grid row"

    # Per-row min/max are independent.
    if len(cells) >= 2:
        luts[0]["min"].setText("10"); luts[0]["max"].setText("20"); luts[0]["auto"] = False
        luts[1]["min"].setText("99"); luts[1]["max"].setText("123"); luts[1]["auto"] = False
        assert _parse_lut(app, 0) == (10.0, 20.0)
        assert _parse_lut(app, 1) == (99.0, 123.0)


def test_generate_runs_with_per_row_display(loaded_app):
    app = loaded_app
    C.image_table_repopulate_dropdowns(app)
    C.image_table_apply_preset(app, "channel", "timepoint")
    luts = app._image_table_lut
    assert luts
    # Turn on top-hat for row 0 only, then render — must not raise and must
    # read row 0's option (True) independently of the other rows (False).
    luts[0]["tophat"].setChecked(True)
    assert _row_display_opts(app, 0)[0] is True
    if len(luts) > 1:
        assert _row_display_opts(app, 1)[0] is False
    C.image_table_generate(app)  # exercises the per-row render path end-to-end


def test_per_row_controls_do_not_auto_generate(loaded_app, monkeypatch):
    """Toggling a row's display options / colormap / Fit must NOT render — only
    the explicit Generate button does (regression for accidental auto-Generate)."""
    import well_viewer.features.image_table.controller as ic

    app = loaded_app
    ic.image_table_repopulate_dropdowns(app)
    ic.image_table_apply_preset(app, "channel", "timepoint")
    luts = app._image_table_lut
    assert luts

    calls = []
    monkeypatch.setattr(ic, "image_table_generate", lambda a: calls.append(a))

    r = sorted(luts)[0]
    luts[r]["tophat"].setChecked(True)
    luts[r]["boundaries"].setChecked(True)
    for i in range(luts[r]["binary"].count()):
        if luts[r]["binary"].itemData(i) == "mask":
            luts[r]["binary"].setCurrentIndex(i)
            break
    color_cbs = getattr(app, "_image_table_row_lut_color_cbs", [])
    if color_cbs:
        try:
            color_cbs[r].setLut("viridis", reversed=False)
        except Exception:
            pass
    ic.image_table_auto_lut(app, r)  # Fit
    assert calls == [], f"per-row controls auto-triggered Generate: {len(calls)} call(s)"
