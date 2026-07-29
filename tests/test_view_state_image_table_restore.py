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


def test_apply_image_table_refreshes_the_builder_shelf(monkeypatch):
    """Regression: ``apply_image_table`` used to set ``row_dims``/``col_dims``/
    ``active_channels`` etc. correctly but never called ``builder_view.
    builder_refresh``, so the drag-and-drop shelf widget (rows/cols trees, Fixed
    panel) kept showing whatever it was built with -- typically the "every
    channel, every timepoint" fresh-tab fallback ``_default_spec`` produces
    before a restore has run -- even though the underlying state was correct.
    This is the actual user-visible "reloads with the wrong parameter" bug:
    the rendered grid and ``ctx.state.image_table`` were fine, only the
    authoring shelf's own widget tree was stale. Verified narrowly (spy on
    builder_refresh) because reconstructing the real shelf's Qt tree to check
    it doesn't show stale chips is far more Qt-plumbing than the behavior
    itself warrants."""
    from types import SimpleNamespace
    from well_viewer.data.crop_tool import CropTool
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    calls = []
    # apply_image_table imports builder_refresh at module scope (no cycle: the
    # reverse edge builder_view -> view_state_io doesn't exist), so patch the
    # name as bound in vsio's own namespace, not builder_view's.
    monkeypatch.setattr(vsio, "builder_refresh", lambda ctx: calls.append(ctx))

    ctx = SimpleNamespace(
        state=SimpleNamespace(image_table=ImageTableState()),
        runtime=SimpleNamespace(image_table=_RuntimeNS(crop_tool=CropTool(on_change=lambda: None), cells=[])),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )
    vsio.apply_image_table(ctx, {"row_dims": ["well"], "col_dims": ["timepoint"]})

    assert calls == [ctx], "apply_image_table must refresh the builder shelf"
    assert ctx.runtime.image_table.restored_this_load is True


def test_image_table_selection_survives_dataset_reload_via_load_directory(image_table_app):
    """A non-trivial (strict-subset, not "every channel/timepoint") Image
    Table grid selection must round-trip through a REAL dataset reload —
    ``app._load_path``, i.e. ``load_controller.load_directory`` end to end —
    not just the hand-rolled ``vs.restore`` + ``vs.apply_pending_tab_state``
    shortcut the other tests in this file use. That shortcut bypasses
    ``load_directory``'s ``on_dataset_loaded`` fan-out (``registry.
    _loaded_image_table``) entirely, which is exactly the gap that let the
    shelf-staleness bug above go unexercised by any full-path test."""
    from well_viewer.features.image_browser.image_table.state import (
        Axis, ChannelSpec, TableSpec,
    )
    from well_viewer.features.image_browser.image_table.build_layout import (
        image_table_apply_spec,
    )
    from well_viewer.features.image_browser.image_table.builder_view import (
        _base_channels,
    )

    app = image_table_app
    ctx = app._ctx

    chans = _base_channels(ctx)
    tps = C._timepoint_options(ctx)
    assert len(chans) >= 2 and len(tps) >= 2, (
        "mini_dataset must offer >=2 channels/timepoints for a real subset test")
    chan, tp = chans[0], tps[0]

    # A strict subset -- one channel, one timepoint -- NOT "every channel,
    # every timepoint" (the bug's fallback default).
    spec = TableSpec(
        rows=Axis(root="well", channels=[ChannelSpec(chan)]),
        cols=Axis(root="timepoint"),
        filters={"timepoint": {tp}},
    )
    image_table_apply_spec(ctx, spec, render=False)
    st = ctx.state.image_table
    assert st.active_channels == {chan}
    assert st.active_timepoints == {tp}
    assert st.row_dims and st.col_dims

    # Land on Image Table as the active leaf so it becomes active_centre_tab.
    app._notebook.setCurrentByName("Image Browser")
    sub = ctx.runtime.image_browser.subtabs
    idx = next(i for i in range(sub.count()) if sub.tabText(i) == "Image Table")
    sub.setCurrentIndex(idx)

    app._persistence.save("view_state")

    # Reload the SAME dataset through the real production load path, with Image
    # Table left as the current tab throughout (matching the reported scenario:
    # the app reopens the same dataset with Image Table already/still active).
    # load_directory's own end-of-load safety net (on_tab_change, added for the
    # separate "wells/channels don't load until you switch tabs" fix) always
    # re-dispatches apply_pending_tab_state against whatever tab is CURRENTLY
    # active -- so Image Table must still be current when _load_path runs, or
    # that final re-apply targets the wrong tab and never touches Image Table.
    data_dir = ctx.state.dataset.data_dir
    app._load_path(data_dir)

    st2 = app._ctx.state.image_table
    assert st2.active_channels == {chan}, (
        f"expected only {chan!r} restored, got {st2.active_channels!r} "
        "-- looks like the fresh-tab reset clobbered the restore")
    assert st2.active_timepoints == {tp}, (
        f"expected only {tp!r} restored, got {st2.active_timepoints!r}")
    assert st2.row_dims and st2.col_dims, "grid was reset to the empty default"


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
