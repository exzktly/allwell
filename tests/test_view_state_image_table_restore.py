"""Regression: the Image Table's display state (crop region, per-cell values, and
per-channel render options) must round-trip through view_state — which is exactly
what Saved Plots save/restore replays.

The render model is headless plain data now (``{"well","chan","tp","fov"}`` dicts)
and per-channel display lives on ``channel_opts`` (set by the builder shelf's ⚙),
so the round-trip is plain JSON — no per-row LUT / per-cell combo widgets.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.persistence import view_state as vs
from well_viewer.features.image_browser.image_table import controller as C
from well_viewer.features.image_browser.image_table import view_state_io as vsio

pytestmark = pytest.mark.qt


class _RuntimeNS:
    """Mimics ``AppRuntime._FeatureRuntime``: attributes set at construction
    resolve; any *unset* attribute returns ``None`` (never ``AttributeError``),
    exactly like the real prefix-backed runtime the ctx-native
    ``_apply_image_table`` reads."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, _name):  # only reached for names not in __dict__
        return None


# ── Fast-lane coverage (no full WellViewerApp → not auto-marked ``slow``) ─────

def test_apply_image_table_crop_restore_does_not_generate():
    """Restoring a crop must NOT fire the CropTool's on_change
    (image_table_generate) — otherwise dataset load composites the whole grid
    off disk. Pure: no QApplication needed (CropTool builds no widgets)."""
    from types import SimpleNamespace
    from well_viewer.data.crop_tool import CropTool
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    calls = []
    ct = CropTool(on_change=lambda: calls.append(1))
    ctx = SimpleNamespace(
        state=SimpleNamespace(image_table=ImageTableState()),
        runtime=SimpleNamespace(image_table=_RuntimeNS(crop_tool=ct, cells=[])),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )
    vsio.apply_image_table(ctx, {"crop": [3, 5, 40, 60], "crop_mode": False})
    assert tuple(ct.crop) == (3, 5, 40, 60)
    assert calls == [], "restore must not trigger image_table_generate on load"


def test_cells_and_channel_opts_round_trip_minimal():
    """Per-cell values (plain dicts) and per-channel render options round-trip
    through snapshot → apply, without constructing the full app."""
    from types import SimpleNamespace
    from well_viewer.data.crop_tool import CropTool
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    cells = [[{"well": "A01", "chan": "mCherry", "tp": "0", "fov": "1"}]]
    st = ImageTableState(rows=1, cols=1)
    st.channel_opts = {"mCherry": {"render": "corrected", "colormap": "magma"}}
    app = SimpleNamespace(
        state=SimpleNamespace(image_table=st),
        runtime=SimpleNamespace(image_table=_RuntimeNS(
            cells=cells, crop_tool=CropTool(on_change=lambda: None))),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )
    app._ctx = app

    snap = vsio.snapshot_image_table(app._ctx)
    assert snap["cells"][0][0]["chan"] == "mCherry"
    assert snap["channel_opts"]["mCherry"]["render"] == "corrected"
    assert snap["channel_opts"]["mCherry"]["colormap"] == "magma"

    # Wipe, then restore through the apply helper.
    cells[0][0]["chan"] = "GFP"
    st.channel_opts = {}
    vsio.apply_image_table(app._ctx, snap)

    assert app._ctx.runtime.image_table.cells[0][0]["chan"] == "mCherry"
    assert st.channel_opts["mCherry"]["colormap"] == "magma"


def test_image_table_display_state_round_trips(image_table_app):
    # image_table_app builds the lazy Image Table sub-tab, so the plain-data
    # render model + crop tool live on ctx.runtime.image_table.
    app = image_table_app
    it = app._ctx.runtime.image_table
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    assert it.cells and it.cells[0], "apply_preset built no render model"

    # ── set a distinctive display state ──────────────────────────────────────
    it.crop_tool.set_crop((3, 5, 40, 60))
    chan0 = it.cells[0][0]["chan"]
    app._ctx.state.image_table.channel_opts = {
        chan0: {"render": "corrected", "colormap": "magma", "lut_min": 100.0}}

    snap = vsio.snapshot_image_table(app._ctx)
    assert snap.get("crop") == [3, 5, 40, 60]
    assert snap["cells"][0][0]["chan"] == chan0
    assert snap["channel_opts"][chan0]["render"] == "corrected"
    assert snap["channel_opts"][chan0]["colormap"] == "magma"

    # ── new session: wipe the state, then restore it ─────────────────────────
    it.crop_tool.clear()
    it.cells[0][0]["chan"] = ""
    app._ctx.state.image_table.channel_opts = {}

    vsio.apply_image_table(app._ctx, snap)

    assert tuple(it.crop_tool.crop) == (3, 5, 40, 60)
    assert it.cells[0][0]["chan"] == chan0
    assert app._ctx.state.image_table.channel_opts[chan0]["colormap"] == "magma"


def test_snapshot_tolerates_unbuilt_image_table(loaded_app):
    """No grid built yet → snapshot is an empty dict, not an exception."""
    app = loaded_app
    app._ctx.runtime.image_table.cells = []
    assert vsio.snapshot_image_table(app._ctx) == {}


def test_channel_restore_through_full_view_state_path(image_table_app):
    """Per-cell values + channel_opts come back through the SAME path Saved Plots
    uses: full ``view_state.snapshot`` → ``view_state.restore`` →
    ``apply_pending_tab_state`` (rather than calling ``_apply_image_table``
    directly)."""
    app = image_table_app
    it = app._ctx.runtime.image_table
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    assert it.cells and it.cells[0], "apply_preset built no render model"

    chan0 = it.cells[0][0]["chan"]
    assert chan0, "cell has no channel"
    app._ctx.state.image_table.channel_opts = {chan0: {"render": "corrected"}}

    snap = vs.snapshot(app._ctx)
    it_snap = (snap.get("tabs") or {}).get("Image Table") or {}
    assert it_snap.get("cells", [[{}]])[0][0].get("chan") == chan0
    assert it_snap["channel_opts"][chan0].get("render") == "corrected"

    # New session: wipe, then replay through the public restore path.
    it.cells[0][0]["chan"] = ""
    app._ctx.state.image_table.channel_opts = {}
    vs.restore(app._ctx, snap)
    vs.apply_pending_tab_state(app._ctx, "Image Table")

    assert it.cells[0][0]["chan"] == chan0
    assert app._ctx.state.image_table.channel_opts[chan0].get("render") == "corrected"
