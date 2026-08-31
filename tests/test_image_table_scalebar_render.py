"""End-to-end: the scale bar is actually burned into a rendered cell.

The pure-logic chrome tests only check the *plan*; they never run the real
``image_table_generate`` paint path, which is how a regression (the toggle
gating the bar off by default) slipped through. This drives the real generate on
the full app and asserts the bar is requested + painted when the user does the
one thing they describe: enter a pixel size (bar length + the on-toggle ship as
defaults).
"""
import numpy as np
import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C
from well_viewer.features.image_browser.image_table import image_compose as IC
from well_viewer.features.image_browser.image_table.state import (
    TableSpec, Axis, ChannelSpec,
)
from well_viewer.features.image_browser.image_table.build_layout import (
    image_table_apply_spec,
)


def test_generate_paints_scale_bar_with_default_toggle(image_table_app, monkeypatch):
    ctx = image_table_app._ctx
    st = ctx.state.image_table
    st.active_wells = set(ctx.state.dataset.well_paths.keys())

    # A structured synthetic image (mini dataset ships no image zips).
    def fake_load(*a, **k):
        arr = np.full((256, 256), 40.0, np.float32)
        arr[60:200, 60:200] = 180.0
        return arr
    monkeypatch.setattr(C, "_load_array", fake_load)

    # Spy on the burn-in so we assert the bar is actually requested per cell.
    seen = {"bar_calls": 0}
    real = IC.draw_cell_chrome
    def spy(rgb, **kw):
        if kw.get("bar_px", 0):
            seen["bar_calls"] += 1
        return real(rgb, **kw)
    monkeypatch.setattr(IC, "draw_cell_chrome", spy)

    image_table_apply_spec(
        ctx, TableSpec(rows=Axis(None, [ChannelSpec("gfp", "corrected")]),
                       cols=Axis("well")), render=False)

    # The user's whole action: enter a pixel size. Toggle + length are defaults.
    assert st.scale_bar_show is True          # shipped default
    st.pixel_size_um = 0.5
    C.image_table_generate(ctx)

    assert seen["bar_calls"] > 0, "scale bar was never burned into any cell"
