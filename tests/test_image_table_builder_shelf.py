"""Image Table builder shelf — spec mutations + the channel-keyed render seam.

Fast (no dataset / no tab build): drives ``builder_view``'s pure spec-mutation
helpers on a lightweight fake ctx, and checks that the four ``image_compose``
render helpers resolve per-channel options from ``channel_opts`` (the seam that
lets display travel with the channel on either axis).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import builder_view as B  # noqa: E402
from well_viewer.features.image_browser.image_table import image_compose as IC  # noqa: E402
from well_viewer.features.image_browser.image_table.state import (  # noqa: E402
    ChannelSpec, spec_to_state_fields,
)


def _fake_ctx(*, wells, channels, timepoints=None, fovs=None):
    from well_viewer.core.app_runtime import AppRuntime
    from well_viewer.features.image_browser.image_table.state import ImageTableState
    info = {"available_timepoints": timepoints or [], "available_fovs": fovs or [],
            "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(active_wells=set(wells)),
        image_view=None,
    )
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    app._ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app))
    return app._ctx


# ── builder_view spec mutations ─────────────────────────────────────────────

def test_default_spec_is_channels_rows_times_cols():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01", "T02"])
    spec = B.builder_spec(ctx)
    assert spec.rows.channels and [c.channel for c in spec.rows.channels] == ["GFP", "RFP"]
    assert spec.cols.root == "timepoint"
    assert spec.channel_axis() == "rows"


def test_move_facet_and_fixed_derivation():
    ctx = _fake_ctx(wells=["A01", "A02"], channels=["gfp"], timepoints=["T01"], fovs=["1", "2"])
    spec = B.builder_spec(ctx)
    # Fixed = every root dim not on an axis. Timepoint is the cols root by default,
    # so Fixed holds Well + FOV.
    assert set(B._fixed_dims(spec)) == {"well", "fov"}
    B._move_facet(ctx, spec, "well", "rows")   # Well becomes the Rows root
    assert spec.rows.root == "well"
    assert "well" not in B._fixed_dims(spec)
    B._move_facet(ctx, spec, "well", "fixed")  # ×-ing it returns it to Fixed
    assert spec.rows.root is None
    assert "well" in B._fixed_dims(spec)


def test_add_remove_channel_and_cohere():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = []                    # start clean
    spec.cols = B.Axis(root="timepoint")
    B._add_channel(ctx, spec, "GFP", "rows")
    B._add_channel(ctx, spec, "RFP", "rows")
    assert [c.channel for c in spec.rows.channels] == ["GFP", "RFP"]
    # dragging a channel to the OTHER axis coheres the whole group there
    B._move_channel(spec, "rows", 0, "cols")
    assert spec.rows.channels == []
    assert [c.channel for c in spec.cols.channels] == ["RFP", "GFP"]
    B._remove_channel(spec, "cols", 0)
    assert [c.channel for c in spec.cols.channels] == ["GFP"]


def test_threshold_channel_flattens_to_binary_token():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = [ChannelSpec("GFP", "threshold")]
    fields = spec_to_state_fields(spec)
    assert fields["active_channels"] == {"GFP (binary)"}
    assert "GFP (binary)" in fields["channel_opts"]


# ── render seam: options resolve from channel_opts by the cell's channel ─────

def test_row_display_opts_from_channel_opts():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"])
    ctx.state.image_table.channel_opts = {
        "GFP": {"channel": "GFP", "render": "mask", "boundary": True, "colormap": "viridis"},
        "DAPI": {"channel": "DAPI", "render": "raw", "boundary": False, "colormap": "Greys"},
    }
    assert IC._row_display_opts(ctx, 0, "GFP") == (False, True, "mask")
    assert IC._row_display_opts(ctx, 0, "DAPI") == (False, False, "")
    assert IC._row_cmap_name(ctx, 0, "GFP") == "viridis"
    assert IC._row_cmap_name(ctx, 0, "DAPI") is None      # Greys → grayscale fast path


def test_threshold_variant_token_resolves_binary():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"])
    ctx.state.image_table.channel_opts = {
        "GFP (binary)": {"channel": "GFP", "render": "threshold", "colormap": "Greys"},
    }
    # keyed by the full "(binary)" token exactly as it appears on the axis
    assert IC._row_display_opts(ctx, 0, "GFP (binary)")[2] == "threshold"


def test_channel_opts_absent_falls_back_to_row_widgets():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"])
    # no channel_opts and no lut widgets → all-off defaults, not an error
    assert IC._row_display_opts(ctx, 0, "GFP") == (False, False, "")
    assert IC._row_log_scale(ctx, 0, "GFP") is False


# ── render is gated on Generate — edits flatten state but never draw ─────────

def test_shelf_apply_never_renders(monkeypatch):
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    seen = []
    monkeypatch.setattr(B, "image_table_apply_spec",
                        lambda c, spec, *, render=True: seen.append(render))
    B._apply_now(ctx)
    assert seen == [False]   # the debounced edit path flattens spec→state, no draw


def test_generate_notify_clears_dirty_cue():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    rt = ctx.runtime.image_table
    rt.builder_dirty = True            # an edit left the table stale
    B.builder_notify_rendered(ctx)     # Generate ran → cue clears
    assert rt.builder_dirty is False


# ── the ⚙ channel menu — must be parented (else GC'd) + render modes on top ───

def test_channel_menu_is_parented_and_lists_render_modes(qtbot):
    from PySide6.QtWidgets import QWidget
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    host = QWidget()
    qtbot.addWidget(host)
    menu = B._channel_menu(ctx, ChannelSpec("GFP", "corrected"), host)
    # Regression: QToolButton.setMenu doesn't own the menu, so a parentless
    # QMenu is garbage-collected and the ⚙ opens nothing. It must be parented.
    assert menu.parent() is host
    labels = [a.text() for a in menu.actions()]
    for mode in ("corrected", "raw", "cell mask", "≥ threshold"):
        assert mode in labels          # render modes one click from the chip
    assert "Boundary outline" in labels


def test_recognised_fluorophores_get_standard_colours():
    # Name-based standard shading, matched case-insensitively as a substring.
    assert B._standard_cmap("GFP") == "Greens"
    assert B._standard_cmap("eGFP") == "Greens"
    assert B._standard_cmap("RFP") == "Reds"
    assert B._standard_cmap("mCherry") == "Reds"
    assert B._standard_cmap("BFP") == "Cyan"
    assert B._standard_cmap("DAPI") == "Blues"
    assert B._standard_cmap("NIR") == "Magenta"
    assert B._standard_cmap("NUC+SEG") is None      # unrecognised → nearest-tint
    # Cyan / Magenta are real, selectable LUTs that ramp black→colour.
    from well_viewer.features.image_browser.image_table.image_compose import LUT_COLOR_NAMES
    assert "Cyan" in LUT_COLOR_NAMES and "Magenta" in LUT_COLOR_NAMES
    from widgets.lut_cmaps import lut_cmap
    assert tuple(round(x) for x in lut_cmap("Magenta")(1.0)[:3]) == (1, 0, 1)


def test_lut_box_shows_each_channel_once():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = [ChannelSpec("GFP"), ChannelSpec("RFP"), ChannelSpec("GFP")]
    spec.cols = B.Axis(root="timepoint")
    table = B._table_channels(spec)
    # GFP appears twice but collapses to a single LUT entry carrying both specs;
    # editing that row fans out to every instance.
    assert list(table.keys()) == ["GFP", "RFP"]
    assert len(table["GFP"]) == 2 and len(table["RFP"]) == 1
    # a threshold variant keeps its own LUT (renders on its own token)
    spec.rows.channels.append(ChannelSpec("GFP", "threshold"))
    assert set(B._table_channels(spec).keys()) == {"GFP", "RFP", "GFP (binary)"}


def test_drop_zones_reject_incompatible_tokens():
    # A zone must not highlight for a token its handler ignores (false affordance).
    assert B._accept_axis("well") and B._accept_axis("newchan:GFP") and B._accept_axis("chan:rows:0")
    assert B._accept_fixed("timepoint")
    assert not B._accept_fixed("chan:rows:0") and not B._accept_fixed("newchan:GFP")
    assert B._accept_tray("chan:rows:0")
    assert not B._accept_tray("well") and not B._accept_tray("newchan:GFP")


def test_root_filter_menu_actually_filters(qtbot):
    from PySide6.QtWidgets import QWidget
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01", "T02", "T03"])
    host = QWidget()
    qtbot.addWidget(host)
    menu = B._root_filter_menu(ctx, "timepoint", host)
    checkable = [a for a in menu.actions() if a.isCheckable()]
    # empty filter ⇒ every value shown/ticked
    assert len(checkable) == 3 and all(a.isChecked() for a in checkable)
    checkable[0].setChecked(False)                     # toggled → commit
    st = ctx.state.image_table
    assert st.active_timepoints == {checkable[1].text(), checkable[2].text()}
    assert ctx.runtime.image_table.builder_dirty is True
    [a for a in menu.actions() if a.text() == "Show all"][0].trigger()
    assert st.active_timepoints == set()               # back to all


def test_channel_menu_actions_mutate_channel_spec(qtbot):
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    qtbot.addWidget(parent)
    cs = ChannelSpec("GFP", "corrected")
    menu = B._channel_menu(ctx, cs, parent)
    [a for a in menu.actions() if a.text() == "cell mask"][0].trigger()
    assert cs.render == "mask"
    [a for a in menu.actions() if a.text() == "Boundary outline"][0].trigger()
    assert cs.boundary is True
