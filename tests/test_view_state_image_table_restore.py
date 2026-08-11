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


def _scope_ctx(st, cells=None):
    """A minimal ctx over *st* — the same shape the round-trip test above uses."""
    from types import SimpleNamespace
    return SimpleNamespace(
        state=SimpleNamespace(image_table=st),
        runtime=SimpleNamespace(image_table=_RuntimeNS(
            cells=cells if cells is not None else [], crop_tool=None)),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )


def test_the_tables_scope_round_trips():
    """The reported bug: restoring a saved Image Table gave back the channels but
    not *what the table was of*.

    Nothing captured ``active_wells`` / ``active_timepoints`` / ``active_fovs``,
    and an empty set does not mean empty — ``grid_layout._axis_dim_values`` reads
    it as **all**. So a saved three-well, one-timepoint grid restored as the whole
    plate across the whole time course, with the right channels on it, which is
    why it read as "the settings did not restore".
    """
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    st = ImageTableState(rows=3, cols=1)
    st.active_wells = {"A01", "A02", "B01"}
    st.active_timepoints = {"0"}
    st.active_fovs = {"1"}
    snap = vsio.snapshot_image_table(_scope_ctx(st, [[{"well": "A01"}]]))

    fresh = ImageTableState()
    vsio.apply_image_table(_scope_ctx(fresh), snap)
    assert fresh.active_wells == {"A01", "A02", "B01"}
    assert fresh.active_timepoints == {"0"}
    assert fresh.active_fovs == {"1"}


def test_the_scope_survives_json():
    """These are ``set``s, which ``json.dump`` refuses — and the block is written
    into ``persistence.json``. A fix that stored them raw would round-trip in
    memory and fail on save, so the serialisable form is the contract."""
    import json
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    st = ImageTableState(rows=1, cols=1)
    st.active_wells = {"B02", "A01"}
    snap = vsio.snapshot_image_table(_scope_ctx(st, [[{"well": "A01"}]]))
    assert snap["active_wells"] == ["A01", "B02"], "wells are stored in plate order"
    json.dumps(snap)


def test_all_wells_round_trips_as_all():
    """An empty selection means "all", so it is omitted rather than written out
    as every well on the plate — and restores to the same table either way."""
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    st = ImageTableState(rows=1, cols=1)
    snap = vsio.snapshot_image_table(_scope_ctx(st, [[{"well": "A01"}]]))
    assert "active_wells" not in snap
    fresh = ImageTableState()
    vsio.apply_image_table(_scope_ctx(fresh), snap)
    assert fresh.active_wells == set()


def test_a_saved_plot_from_before_this_does_not_clear_the_live_selection():
    """A block with no scope keys leaves the live plate alone.

    Deliberately unlike the ``row_dims`` handling beside it, which writes ``[]``
    for a missing key: every plot saved before the scope was captured is silent
    about it, and wiping the user's plate selection on their behalf would be a
    second bug rather than a graceful fallback."""
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    st = ImageTableState()
    st.active_wells = {"C05"}
    st.active_timepoints = {"12"}
    vsio.apply_image_table(_scope_ctx(st), {"rows": 1, "cols": 1})
    assert st.active_wells == {"C05"}
    assert st.active_timepoints == {"12"}


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


def test_restoring_a_saved_plot_does_not_regenerate_the_grid(image_table_app):
    """Restore puts back the shelf, the scope and the LUTs — and does NOT read
    the images back off disk.

    The tab is generation-gated on purpose: a grid can be hundreds of frames, and
    deciding to pay for that is the user's call, on Restore exactly as on a
    dataset load. What Restore owes them instead is not pretending the pictures
    match the controls, so the restored table is marked stale and the shelf
    header carries its "edited — press Generate" cue.
    """
    from well_viewer.core.ui_helpers import clear_layout
    from well_viewer.features.saved_plots.controller import (
        restore_saved_plot, save_plot_from_card,
    )
    from well_viewer.features.saved_plots.state import get_store

    app = image_table_app
    ctx = app._ctx
    C.image_table_repopulate_dropdowns(ctx)
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    assert ctx.runtime.image_table.cells, "apply_preset built no render model"

    assert save_plot_from_card(ctx, "Image Table", b"\x89PNG", "grid").startswith("Saved")
    rec = get_store(ctx).records[-1]

    grid = ctx.runtime.image_table.render_grid
    assert grid is not None
    clear_layout(grid)
    ctx.runtime.image_table.builder_dirty = False

    restore_saved_plot(ctx, rec.id)

    assert grid.count() == 0, "Restore regenerated the grid off disk"
    assert ctx.runtime.image_table.builder_dirty is True, (
        "restored table was not marked stale, so the shelf claims the grid is "
        "of the settings it now shows")


def test_the_image_table_owns_a_per_tab_block(image_table_app):
    """The predicate `saved_plots` uses to tell "this tab keeps nothing" from
    "this record predates what it keeps"."""
    ctx = image_table_app._ctx
    assert vs.owns_tab_block(ctx, "Image Table") is True
    assert vs.owns_tab_block(ctx, "Bar Plots") is True
    assert vs.owns_tab_block(ctx, "Line Graphs") is False


def test_a_record_with_no_image_table_block_says_so(image_table_app):
    """A saved plot from before the tab recorded its settings restores the
    cross-cutting state and nothing tab-specific. Silence there reads as a
    broken Restore, and the user re-clicks it rather than re-saving the plot —
    the one thing that would actually fix it."""
    from well_viewer.features.saved_plots.controller import restore_saved_plot
    from well_viewer.features.saved_plots.state import SavedPlot, get_store

    app = image_table_app
    ctx = app._ctx
    said = []
    ctx.notify.set_status = lambda msg: said.append(msg)  # boundary, not state

    store = get_store(ctx)
    store.ensure_loaded(ctx)
    store.records.append(SavedPlot(
        id="old-1", created_at="2026-01-01T00:00:00", tab="Image Table",
        title="an old grid", png_filename="old-1.png",
        params={"active_centre_tab": "Image Table"},   # no "tabs" block at all
        thresholds={}))

    restore_saved_plot(ctx, "old-1")
    assert said, "restoring a record with no Image Table block said nothing"
    assert "Image Table" in said[-1] and "an old grid" in said[-1]
