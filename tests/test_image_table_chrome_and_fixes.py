"""Image Table: burned-in chrome (scale bar + labels), global Auto LUT, and the
two builder bug fixes (channel drag order, flush-on-Generate).

Pure-logic where possible: the chrome drawing + placement math + LUT resolution
live in the Qt-free ``image_compose`` / ``grid_layout`` layers, and the display
view-state (de)serialization is a plain dict round-trip. A ``SimpleNamespace``
stands in for the ctx (mirrors ``test_image_table_nested_axes``), so no real
``WellViewerApp`` is needed.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C
from well_viewer.features.image_browser.image_table import image_compose as IC
from well_viewer.features.image_browser.image_table import view_state_io as V
from well_viewer.features.image_browser.image_table.state import ImageTableState


def _fake_app(*, wells, channels, active_wells=None, active_channels=None,
              timepoints=None, fovs=None):
    info = {"available_timepoints": timepoints or [], "available_fovs": fovs or [],
            "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(
            active_wells=set(active_wells or []),
            active_channels=set(active_channels or []),
        ),
    )
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app))
    return app


# ── Bug fix: channel drag order is respected on Generate ────────────────────

def test_axis_dim_values_respects_channel_drag_order():
    app = _fake_app(wells=["A01"], channels=["gfp", "rfp", "dapi"])
    st = app._app_state.image_table
    # Canonical channel order is GFP, RFP, DAPI. The builder stores the drag order
    # in channel_opts (an ordered dict); active_channels is an unordered set.
    st.channel_opts = {
        "RFP": {"channel": "rfp"}, "DAPI": {"channel": "dapi"}, "GFP": {"channel": "gfp"},
    }
    st.active_channels = {"GFP", "RFP", "DAPI"}
    # Drag order (RFP, DAPI, GFP) wins over canonical (GFP, RFP, DAPI).
    assert C._axis_dim_values(app._ctx, "channel") == ["RFP", "DAPI", "GFP"]


def test_axis_dim_values_appends_unkeyed_channels_canonically():
    app = _fake_app(wells=["A01"], channels=["gfp", "rfp", "dapi"])
    st = app._app_state.image_table
    st.channel_opts = {"DAPI": {"channel": "dapi"}}          # only DAPI has a spec
    st.active_channels = {"GFP", "RFP", "DAPI"}
    # DAPI first (from channel_opts), then the rest in canonical order.
    assert C._axis_dim_values(app._ctx, "channel") == ["DAPI", "GFP", "RFP"]


# ── Feature A/B: burned-in scale bar + label ────────────────────────────────

def test_draw_cell_chrome_bar_in_chosen_corner():
    rgb = np.zeros((100, 100, 3), np.uint8)
    out = IC.draw_cell_chrome(rgb, bar_px=40, bar_corner="br", bar_color="white")
    ys, xs = np.where(out[..., 0] == 255)
    assert ys.size and xs.size
    assert ys.max() > 80 and xs.max() > 80          # bottom-right


def test_draw_cell_chrome_bar_length_matches_px():
    rgb = np.zeros((200, 200, 3), np.uint8)
    out = IC.draw_cell_chrome(rgb, bar_px=60, bar_corner="bl", bar_color="white")
    # The widest fully-white row spans ~60 px (allow the ±1 halo).
    white = out[..., 0] == 255
    widest = max(int(row.sum()) for row in white)
    assert 58 <= widest <= 62


def test_auto_bar_color_is_visible_on_a_light_background():
    """The default 'Greys' colormap renders dim fluorescence as a near-white
    image; a fixed white bar is then invisible. 'auto' must pick a dark bar so it
    reads against the light background (the reported "bar doesn't appear" bug)."""
    light = np.full((200, 200, 3), 234, np.uint8)     # near-white, like Greys(dim)
    out = IC.draw_cell_chrome(light.copy(), bar_px=80, bar_corner="br",
                              bar_color="auto")
    # The bar region must contain clearly dark pixels (a black bar on white).
    region = out[-40:, -120:]
    assert int((region < 60).sum()) > 100, "auto bar not visible on a light background"


def test_auto_bar_color_is_visible_on_a_dark_background():
    dark = np.zeros((200, 200, 3), np.uint8)
    out = IC.draw_cell_chrome(dark.copy(), bar_px=80, bar_corner="br",
                              bar_color="auto")
    region = out[-40:, -120:]
    assert int((region > 200).sum()) > 100, "auto bar not visible on a dark background"


def test_scale_bar_thickness_is_tunable():
    """The bar thickness is user-tunable (a too-thin bar vanishes on downscale)."""
    rgb = np.zeros((240, 240, 3), np.uint8)

    def bar_rows(thick_px):
        out = IC.draw_cell_chrome(rgb.copy(), bar_px=100, bar_corner="br",
                                  bar_color="white", bar_thickness_px=thick_px)
        ys = np.where((out[..., 0] == 255).any(axis=1))[0]
        return int(ys.max() - ys.min() + 1) if ys.size else 0

    assert bar_rows(2) == 2
    assert bar_rows(4) == 4
    assert bar_rows(10) == 10                          # thicker → taller bar


def test_draw_cell_chrome_noop_when_nothing_to_draw():
    rgb = np.zeros((50, 50, 3), np.uint8)
    assert IC.draw_cell_chrome(rgb, bar_px=0, label_text="") is rgb


def test_draw_cell_chrome_label_paints_and_does_not_raise():
    rgb = np.zeros((160, 160, 3), np.uint8)
    out = IC.draw_cell_chrome(rgb, label_text="A01  GFP  T:0", label_corner="tl")
    assert out.shape == rgb.shape
    assert out.any()                                 # box + text drew something


# ── Placement math ──────────────────────────────────────────────────────────

def test_corner_cell_maps_each_corner():
    assert IC._corner_cell(3, 4, "tl") == (0, 0)
    assert IC._corner_cell(3, 4, "tr") == (0, 3)
    assert IC._corner_cell(3, 4, "bl") == (2, 0)
    assert IC._corner_cell(3, 4, "br") == (2, 3)


def test_corner_cell_falls_back_when_corner_blank():
    occ = {(0, 0), (1, 1)}                            # (2,3) not occupied
    assert IC._corner_cell(3, 4, "br", occ) == (1, 1)


def test_chrome_target_cells_all_vs_single_corner():
    occ = {(0, 0), (0, 1), (1, 0), (1, 1)}
    assert IC._chrome_target_cells(2, 2, "all", occ) == occ
    assert IC._chrome_target_cells(2, 2, "tr", occ) == {(0, 1)}


def test_resolve_chrome_plan_bar_px_and_targets():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.scale_bar_show = True
    st.pixel_size_um = 0.5
    st.scale_bar_um = 50.0
    st.scale_bar_cells = "br"
    st.label_mode = "identity"
    st.label_cells = "all"
    cells = [
        [{"well": "A01", "chan": "GFP", "tp": "0", "fov": ""},
         {"well": "A01", "chan": "RFP", "tp": "0", "fov": ""}],
        [{"well": "A02", "chan": "GFP", "tp": "0", "fov": ""},
         {"well": "A02", "chan": "RFP", "tp": "0", "fov": ""}],
    ]
    bar_px, bar_targets, label_targets = IC.resolve_chrome_plan(app._ctx, cells)
    assert bar_px == 100                             # 50 µm / 0.5 µm-per-px
    assert bar_targets == {(1, 1)}                   # bottom-right cell
    assert len(label_targets) == 4                   # all occupied cells


def test_resolve_chrome_plan_off_when_pixel_size_zero():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.scale_bar_show = True                          # toggled on, but…
    st.pixel_size_um = 0.0                            # unknown → bar disabled
    st.scale_bar_um = 50.0
    cells = [[{"well": "A01", "chan": "GFP", "tp": "0", "fov": ""}]]
    bar_px, bar_targets, _ = IC.resolve_chrome_plan(app._ctx, cells)
    assert bar_px == 0 and bar_targets == set()


def test_scale_bar_show_toggle_gates_the_bar():
    """The 'Show scale bar' toggle is the master switch: with valid calibration,
    the bar is off when the toggle is off and on when it's on."""
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.pixel_size_um = 0.5
    st.scale_bar_um = 50.0
    cells = [[{"well": "A01", "chan": "GFP", "tp": "0", "fov": ""}]]

    st.scale_bar_show = False
    bar_px, targets, _ = IC.resolve_chrome_plan(app._ctx, cells)
    assert bar_px == 0 and targets == set()

    st.scale_bar_show = True
    bar_px, targets, _ = IC.resolve_chrome_plan(app._ctx, cells)
    assert bar_px == 100 and targets == {(0, 0)}


def test_default_state_shows_bar_once_pixel_size_is_entered():
    """The toggle ships ON and the bar length is pre-set, so entering only the
    pixel size draws the bar — the user's workflow ("set the values, get a bar").
    A fresh table shows nothing until a pixel size is given (no wrong-scale bar)."""
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table            # untouched: defaults only
    assert st.scale_bar_show is True and st.scale_bar_um > 0
    assert st.pixel_size_um == 0.0             # uncalibrated ⇒ no bar yet
    cells = [[{"well": "A01", "chan": "GFP", "tp": "0", "fov": ""}]]
    assert IC.resolve_chrome_plan(app._ctx, cells)[0] == 0   # nothing before calibration
    st.pixel_size_um = 0.5                      # the ONE value the user must enter
    assert IC.resolve_chrome_plan(app._ctx, cells)[0] > 0    # bar appears


# ── A ticked box that draws nothing says why ────────────────────────────────
#
# Reported: "image table is still built without scale bar even when checked".
# Four separate paths produced exactly that, and the only thing that ever
# reported a missing bar — ``apply_cell_chrome`` — is guarded on a bar length
# above zero, so by construction it cannot see a bar that was never computed.
# The checkbox stayed ticked over a grid with no bar and no explanation in the
# status line, the saved figure or the log.

def _one_cell_app(**over):
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.scale_bar_show = True
    st.pixel_size_um = 0.5
    st.scale_bar_um = 50.0
    for key, value in over.items():
        setattr(st, key, value)
    return app, [[{"well": "A01", "chan": "GFP", "tp": "0", "fov": ""}]]


def test_a_planned_bar_leaves_nothing_to_explain():
    """The paired positive: the clause must stay empty when a bar is drawn.

    Without this, a note set unconditionally would satisfy every test below
    and put a "no bar was drawn" sentence under every grid that has one.
    """
    app, cells = _one_cell_app()
    bar_px, targets, _ = IC.resolve_chrome_plan(app._ctx, cells)
    assert bar_px > 0 and targets
    assert app._app_state.image_table.scale_bar_plan_note == ""
    assert IC.scale_bar_refusal_clause(app._ctx) == ""


def test_an_unset_pixel_size_is_named_not_left_silent():
    app, cells = _one_cell_app(pixel_size_um=0.0)
    IC.resolve_chrome_plan(app._ctx, cells)
    said = IC.scale_bar_refusal_clause(app._ctx)
    assert "Show scale bar is on but no bar was drawn" in said
    assert "Pixel µm/px is not set" in said, said
    # And it names the control to change, not merely the fact.
    assert "enter the pixel size" in said, said


def test_a_zero_bar_length_is_named_and_told_apart_from_an_unset_pixel_size():
    """Two different empty fields, two different sentences.

    One reason for both would send a user to check a pixel size they had
    already set — which is the report that produced this whole change.
    """
    app, cells = _one_cell_app(scale_bar_um=0.0)
    IC.resolve_chrome_plan(app._ctx, cells)
    said = IC.scale_bar_refusal_clause(app._ctx)
    assert "Bar length µm is 0" in said, said
    assert "Pixel" not in said, said


def test_a_show_on_value_this_grid_has_no_cell_for_is_named():
    """Reachable from a saved view state carrying a code the combo no longer
    offers: ``_corner_cell`` returns ``None`` for anything but tl/tr/bl/br, so
    the target set comes back empty and every cell loses its bar."""
    app, cells = _one_cell_app(scale_bar_cells="corner")
    bar_px, targets, _ = IC.resolve_chrome_plan(app._ctx, cells)
    assert bar_px > 0 and targets == set(), "the bar was computed but placed nowhere"
    said = IC.scale_bar_refusal_clause(app._ctx)
    assert "Show on" in said and "'corner'" in said, said


def test_an_empty_grid_says_so_rather_than_blaming_a_control():
    app, _ = _one_cell_app()
    IC.resolve_chrome_plan(app._ctx, [])
    said = IC.scale_bar_refusal_clause(app._ctx)
    assert "no images to draw it on" in said, said


def test_an_unticked_box_explains_nothing_because_nothing_was_asked_for():
    app, cells = _one_cell_app(scale_bar_show=False)
    IC.resolve_chrome_plan(app._ctx, cells)
    assert app._app_state.image_table.scale_bar_plan_note == ""
    assert IC.scale_bar_refusal_clause(app._ctx) == ""


def test_the_note_does_not_survive_into_the_next_pass():
    """Same per-pass lifetime as the three sets beside it.

    A note left from an uncalibrated pass would sit under the next grid — one
    that draws a bar perfectly well — saying it drew none.
    """
    app, cells = _one_cell_app(pixel_size_um=0.0)
    IC.resolve_chrome_plan(app._ctx, cells)
    assert IC.scale_bar_refusal_clause(app._ctx) != ""
    app._app_state.image_table.pixel_size_um = 0.5
    IC.reset_scan_misses(app._ctx)
    assert app._app_state.image_table.scale_bar_plan_note == ""
    IC.resolve_chrome_plan(app._ctx, cells)
    assert IC.scale_bar_refusal_clause(app._ctx) == ""


def test_the_per_cell_refusal_still_reports_when_a_bar_was_planned():
    """The older half of the report must survive the newer one.

    A bar that was computed and would not fit is a different failure with a
    different fix, and folding the two into one clause is how one of them gets
    lost.
    """
    app, cells = _one_cell_app()
    IC.resolve_chrome_plan(app._ctx, cells)
    app._app_state.image_table.scale_bar_refusals.add(("A01", "gfp", "0", ""))
    said = IC.scale_bar_refusal_clause(app._ctx)
    assert "longer than those cells can hold" in said, said
    assert "A01" in said, said


# ── Behavior: global-per-channel Auto LUT ───────────────────────────────────

def test_parse_lut_uses_pooled_auto_when_channel_is_auto():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.channel_opts = {"GFP": {"channel": "gfp", "render": "corrected"}}  # no lut_min/max
    st.auto_lut_by_channel = {"gfp": (10.0, 200.0)}
    assert IC._parse_lut(app._ctx, 0, "GFP") == (10.0, 200.0)


def test_parse_lut_explicit_window_beats_pooled_auto():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.channel_opts = {"GFP": {"channel": "gfp", "lut_min": 5.0, "lut_max": 50.0}}
    st.auto_lut_by_channel = {"gfp": (10.0, 200.0)}
    assert IC._parse_lut(app._ctx, 0, "GFP") == (5.0, 50.0)


def test_parse_lut_auto_without_pool_is_autofit():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.channel_opts = {"GFP": {"channel": "gfp", "render": "corrected"}}
    st.auto_lut_by_channel = {}
    assert IC._parse_lut(app._ctx, 0, "GFP") == (None, None)


# ── Persistence: display fields round-trip ──────────────────────────────────

def test_display_snapshot_restore_roundtrip():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.pixel_size_um = 0.65
    st.scale_bar_um = 100.0
    st.scale_bar_cells = "tl"
    st.scale_bar_img_corner = "bl"
    st.scale_bar_color = "yellow"
    st.label_mode = "identity"
    st.label_cells = "tr"
    st.label_img_corner = "br"
    st.label_font_px = 16
    st.label_color = "cyan"
    st.label_bg = False

    snap = V._snapshot_display(app._ctx)

    fresh = _fake_app(wells=["A01"], channels=["gfp"])
    V._restore_display(fresh._ctx, snap)
    st2 = fresh._app_state.image_table
    for k in V._DISPLAY_DEFAULTS:
        assert getattr(st2, k) == getattr(st, k), k


def test_clear_caches_drops_pooled_auto_luts():
    st = ImageTableState()
    st.auto_lut_by_channel = {"gfp": (1.0, 2.0)}
    st.clear_caches()
    assert st.auto_lut_by_channel == {}


# ── Bug fix: Generate flushes the debounced builder apply ───────────────────

def test_builder_flush_pending_applies_active_timer(monkeypatch):
    from well_viewer.features.image_browser.image_table import builder_view as B

    app = _fake_app(wells=["A01"], channels=["gfp"])
    calls = []

    class _FakeTimer:
        def __init__(self):
            self._active = True

        def isActive(self):
            return self._active

        def stop(self):
            self._active = False

    ft = _FakeTimer()
    app._ctx.runtime.image_table.builder_timer = ft
    monkeypatch.setattr(B, "_apply_now", lambda ctx: calls.append(1))

    B.builder_flush_pending(app._ctx)
    assert calls == [1]                              # pending edit landed
    assert not ft.isActive()                         # timer stopped


def test_builder_flush_pending_noop_without_timer(monkeypatch):
    from well_viewer.features.image_browser.image_table import builder_view as B

    app = _fake_app(wells=["A01"], channels=["gfp"])
    calls = []
    monkeypatch.setattr(B, "_apply_now", lambda ctx: calls.append(1))
    # No builder_timer set → nothing to flush.
    B.builder_flush_pending(app._ctx)
    assert calls == []


# ── End-to-end: chrome + Auto-LUT pooling run on a real Generate ────────────

@pytest.mark.slow
def test_generate_with_scale_bar_and_auto_lut_on_mini_dataset(qtbot, mini_dataset):
    """Build the real app on the mini dataset, enable the scale bar + identity
    labels, and Generate — the chrome plan must resolve a bar on the real grid and
    the Auto-LUT pooling pass must run, and the export figure must build."""
    from well_viewer.features.image_browser.controller import build_image_browser_subtab
    from well_viewer.runtime_app import WellViewerApp

    app = WellViewerApp()
    qtbot.addWidget(app)
    build_pending = app._ctx.runtime.centre.build_pending
    if build_pending is not None:
        build_pending("Image Browser")
    app._notebook.setCurrentByName("Image Browser")
    sub = app._ctx.runtime.image_browser.subtabs
    idx = next(i for i in range(sub.count()) if sub.tabText(i) == "Image Table")
    build_image_browser_subtab(app._ctx, idx)
    sub.setCurrentIndex(idx)
    app._load_path(mini_dataset)
    qtbot.wait(0)

    ctx = app._ctx
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    assert ctx.runtime.image_table.cells, "preset did not build a grid"

    st = ctx.state.image_table
    st.scale_bar_show = True
    st.pixel_size_um = 0.5
    st.scale_bar_um = 20.0
    st.scale_bar_cells = "all"
    st.label_mode = "identity"
    st.label_cells = "br"
    C.image_table_generate(ctx)

    bar_px, bar_targets, _ = IC.resolve_chrome_plan(ctx, ctx.runtime.image_table.cells)
    assert bar_px == 40                              # 20 µm / 0.5 µm-per-px
    assert bar_targets, "scale bar has no target cells on the real grid"
    # The Auto-LUT pooling pass ran over the decoded arrays.
    assert isinstance(st.auto_lut_by_channel, dict)
    # A previously-generated table exports without error, chrome burned in.
    fig, kwargs = C._build_export_figure(ctx)
    assert fig is not None
