"""Playground tab — interactive single-frame background-correction testbed.

Covers: the shared ``correct_single_frame`` parity with the pipeline primitives,
the pure ``build_playground_rgb`` renderer, the reference-field worker compute,
and the controller's load→correct→render orchestration on an offscreen app.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

import process_microscopy as pm

# Heavy: builds the Qt Playground worker/UI; ~4 min. Excluded from the fast CI
# lane (`-m "not slow"`); run explicitly in the local/manual full pass.
pytestmark = pytest.mark.slow


# ── shared correction (pure, no Qt) ──────────────────────────────────────────

def _frame(seed=0):
    rng = np.random.default_rng(seed)
    return (rng.random((48, 48)) * 1000).astype(np.float32)


def test_correct_single_frame_parity_per_image():
    img = _frame()
    # gaussian flatfield + sliding_disk == compose the primitives directly
    got = pm.correct_single_frame(img, flatfield_mode="gaussian",
                                  background_method="sliding_disk", radius=15)
    want = pm.subtract_background(
        pm.apply_flatfield(img, pm.estimate_gaussian_flatfield(img)), 15,
        "sliding_disk")
    assert np.allclose(got, want)
    # robust_mesh params flow through unchanged
    got2 = pm.correct_single_frame(img, background_method="robust_mesh",
                                   radius=20, mesh_ratio=4.0, mesh_sigma=2.0)
    want2 = pm.subtract_background(img, 20, "robust_mesh", mesh_ratio=4.0,
                                   mesh_sigma=2.0)
    assert np.allclose(got2, want2)


def test_correct_single_frame_parity_combined_empty_well():
    img = _frame(1)
    from pipeline.flatfield import normalize_flatfield
    B = pm.estimate_background_field([img, img * 0.9 + 5, img * 1.1])
    normB = normalize_flatfield(B)
    got = pm.correct_single_frame(img, flatfield_mode="empty_well",
                                  background_method="empty_well",
                                  bg_field=B, ff_field=normB)
    want = pm.combined_empty_well_correct(img, B, normB)
    assert np.allclose(got, want)


def test_correct_single_frame_none_is_passthrough_float32():
    img = _frame(2)
    out = pm.correct_single_frame(img, flatfield_mode="none",
                                  background_method="none")
    assert out.dtype == np.float32 and np.allclose(out, img)


# ── pure renderer ────────────────────────────────────────────────────────────

def test_build_playground_rgb_shapes_and_overlay():
    from well_viewer.features.image_browser.playground.renderer import build_playground_rgb
    arr = np.zeros((10, 10), dtype=np.float32)
    arr[2:8, 2:8] = 500.0
    mask = np.zeros((10, 10), dtype=np.int32)
    mask[3:7, 3:7] = 1  # one cell
    rgb = build_playground_rgb(arr, mask, lo=0, hi=500, cmap_name="",
                               binary=False, show_boundary=True)
    assert rgb.shape == (10, 10, 3) and rgb.dtype == np.uint8
    # the cell's boundary pixels are painted white
    assert (rgb[3, 3] == (255, 255, 255)).all()


def test_build_playground_rgb_binary_whitens_cells():
    from well_viewer.features.image_browser.playground.renderer import build_playground_rgb
    arr = np.zeros((8, 8), dtype=np.float32)
    mask = np.zeros((8, 8), dtype=np.int32)
    mask[2:5, 2:5] = 7
    rgb = build_playground_rgb(arr, mask, binary=True)
    assert (rgb[3, 3] == (255, 255, 255)).all()    # inside the cell
    assert (rgb[0, 0] == (0, 0, 0)).all()          # background black


# ── reference-field worker compute ───────────────────────────────────────────

def test_build_reference_fields_estimates(monkeypatch):
    from well_viewer.features.image_browser.playground import worker
    import well_viewer.features.image_browser.image_table.controller as itc
    import well_viewer.data.image_discovery as disc
    frames = [_frame(i) for i in range(4)]
    # The worker imports these lazily from their source modules; patch there.
    monkeypatch.setattr(
        itc, "_load_well_channel",
        lambda *a, **k: {(str(i), "0"): f for i, f in enumerate(frames)})
    monkeypatch.setattr(disc, "open_imgref_as_array", lambda ref, **k: ref)

    ff, bg = worker.build_reference_fields(
        None, "H12", "gfp", flatfield_mode="basic", background_method="none")
    assert ff is not None and bg is None and ff.shape == frames[0].shape

    ff2, bg2 = worker.build_reference_fields(
        None, "H12", "gfp", flatfield_mode="empty_well",
        background_method="empty_well")
    assert bg2 is not None and ff2 is not None       # combined builds both


def test_build_reference_fields_empty_returns_none(monkeypatch):
    from well_viewer.features.image_browser.playground import worker
    import well_viewer.features.image_browser.image_table.controller as itc
    monkeypatch.setattr(itc, "_load_well_channel", lambda *a, **k: {})
    ff, bg = worker.build_reference_fields(
        None, "H12", "gfp", flatfield_mode="basic", background_method="empty_well")
    assert ff is None and bg is None


# ── controller orchestration (offscreen app) ─────────────────────────────────

@pytest.fixture
def playground_app(loaded_app):
    """A loaded app with the Playground sub-tab built."""
    from well_viewer.features.image_browser.playground.tab_view import build_playground_tab
    from PySide6.QtWidgets import QWidget, QHBoxLayout
    frame = QWidget()
    QHBoxLayout(frame)
    build_playground_tab(loaded_app._ctx, frame)
    # Keep the frame (and its child widgets) alive for the test's lifetime —
    # otherwise Qt garbage-collects the combos referenced via app._playground_*.
    loaded_app._test_playground_frame = frame
    return loaded_app


def test_tab_builds_and_populates_selectors(playground_app):
    app = playground_app
    st = app._app_state.playground
    assert hasattr(app, "_playground_image_label")
    assert st.well and st.channel and st.timepoint and st.fov
    # virtual channels are excluded from the playground channel dropdown
    items = [app._playground_chan_cb.itemText(i)
             for i in range(app._playground_chan_cb.count())]
    assert "NUC+SEG" not in items


def test_well_is_driven_by_the_sidebar_plate(playground_app, monkeypatch):
    """The frame's well comes from the ever-present sidebar plate, not a
    duplicated in-tab plate. Selecting on the sidebar drives ``st.well`` via the
    ``on_selection_changed`` hook; from a multi-well selection the lowest-rank
    well is taken."""
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    monkeypatch.setattr(C, "_load_array",
                        lambda *a, **k: np.full((32, 32), 100.0, np.float32))
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: None)

    # No duplicated in-tab plate / old Well combo — the left-rail plate is used.
    assert not hasattr(app, "_playground_well_plate")
    assert not hasattr(app, "_playground_well_cb")

    # Registered as a single-well tab so the sidebar plate goes single-select.
    assert app._features.get("Playground").single_well_selection is True

    wells = sorted(app._app_state.dataset.well_paths.keys())
    assert len(wells) >= 2

    # A sidebar-plate selection drives the frame well.
    app._app_state.selection.selected_wells = {wells[1]}
    C.playground_sync_selected_well(app._ctx)
    assert st.well == wells[1]

    # From a multi-well selection (carried in from another tab) the lowest
    # plate-rank well is chosen.
    app._app_state.selection.selected_wells = {wells[0], wells[1]}
    assert C.playground_first_selected_well(app._ctx) == wells[0]
    C.playground_sync_selected_well(app._ctx)
    assert st.well == wells[0]


def test_on_change_corrects_and_renders(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    frame = _frame(5)
    mask = np.zeros_like(frame, dtype=np.int32)
    mask[10:20, 10:20] = 1
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: frame)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: mask)

    app._playground_bg_cb.setCurrentText("sliding_disk")
    app._playground_radius_edit.setText("12")
    st.source = "corrected"
    C.playground_on_change(app._ctx)
    assert st.base_rgb is not None
    assert app._playground_image_label.pixmap() is not None
    corrected_rgb = st.base_rgb.copy()

    # Raw vs Corrected differ for a non-none method.
    st.source = "raw"
    C.playground_on_change(app._ctx)
    assert not np.array_equal(corrected_rgb, st.base_rgb)


def test_source_segmented_control_has_three_stages(playground_app):
    seg = playground_app._playground_source_seg
    for stage in ("raw", "flatfield", "corrected"):
        seg.setCurrentByData(stage)
        assert seg.currentData() == stage


def test_flatfield_source_applies_only_flatfield(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    import process_microscopy as pm
    frame = _frame(7)
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: frame)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: None)
    # flatfield_mode / background_method are read from the widgets each change.
    app._playground_ff_cb.setCurrentText("gaussian")
    app._playground_bg_cb.setCurrentText("sliding_disk")
    st.source = "flatfield"
    C.playground_on_change(app._ctx)
    # Flat-field source == flatfield only (background = none).
    want = pm.correct_single_frame(frame, flatfield_mode="gaussian",
                                   background_method="none")
    assert np.allclose(st.display_arr, want)


def test_local_threshold_binary_does_not_touch_app_wide(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    from well_viewer.core.channel_state_controller import get_thresh_frac_on
    frame = np.zeros((20, 20), dtype=np.float32)
    frame[2:6, 2:6] = 1000.0     # bright cell
    frame[12:16, 12:16] = 10.0   # dim cell
    mask = np.zeros((20, 20), dtype=np.int32)
    mask[2:6, 2:6] = 1
    mask[12:16, 12:16] = 2
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: frame)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: mask)

    before = get_thresh_frac_on(app._ctx, (st.channel or "").lower())
    st.source = "raw"
    st.binary_mask = True
    st.threshold = 100.0          # local override between the two cells
    C.playground_on_change(app._ctx)
    rgb = st.base_rgb
    assert (rgb[3, 3] == (255, 255, 255)).all()    # bright cell ON
    assert (rgb[14, 14] == (0, 0, 0)).all()        # dim cell OFF
    # The app-wide ThreshFracOn is untouched.
    assert get_thresh_frac_on(app._ctx, (st.channel or "").lower()) == before


def test_qc_size_filter_drops_out_of_range_cells(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    img = np.zeros((40, 40), dtype=np.float32)
    mask = np.zeros((40, 40), dtype=np.int32)
    mask[2:5, 2:5] = 1
    img[2:5, 2:5] = 1000.0            # small cell (area 9)
    mask[10:20, 10:20] = 2
    img[10:20, 10:20] = 1000.0        # big cell (area 100)
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: img)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: mask)
    st.source = "raw"
    st.binary_mask = True
    st.threshold = 10.0
    C.playground_on_change(app._ctx)
    assert int((st.base_rgb == 255).all(axis=2).sum()) == 109   # both cells
    # qc_min_area is read from the widget each change — drive the widget.
    app._playground_qc_min_area_edit.setText("20")
    C.playground_on_change(app._ctx)
    assert int((st.base_rgb == 255).all(axis=2).sum()) == 100   # small one dropped


def test_per_source_lut_memory(playground_app, monkeypatch):
    app = playground_app
    from well_viewer.features.image_browser.playground import controller as C
    img = _frame(11)
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: img)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: None)
    seg = app._playground_source_seg
    seg.setCurrentByData("raw")
    app._playground_lmin_entry.setText("10")
    app._playground_lmax_entry.setText("800")
    app._playground_lmin_entry.editingFinished.emit()
    seg.setCurrentByData("corrected")
    assert app._playground_lmin_entry.text() == "auto"      # corrected: own (auto)
    seg.setCurrentByData("raw")
    assert (app._playground_lmin_entry.text(), app._playground_lmax_entry.text()) == ("10", "800")


def test_pan_updates_state(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: _frame(12))
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: None)
    st.source = "raw"
    C.playground_on_change(app._ctx)

    class _Ev:
        def __init__(self, x, y):
            self._x, self._y = x, y

        def position(self):
            from PySide6.QtCore import QPointF
            return QPointF(self._x, self._y)
    before = (st.pan_x, st.pan_y)
    C.playground_pan_press(app._ctx, _Ev(50, 50))
    C.playground_pan_move(app._ctx, _Ev(70, 65))
    assert (st.pan_x - before[0], st.pan_y - before[1]) == (20.0, 15.0)


def test_hover_tooltip_reads_display_value(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    frame = _frame(9)
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: frame)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: None)
    st.source = "raw"
    C.playground_on_change(app._ctx)
    # display_arr is the shown frame; geometry was recorded for the mapping.
    assert st.display_arr is not None
    assert app._playground_disp["iw"] == frame.shape[1]

    class _Ev:
        def position(self):
            class _P:
                def x(self_inner): return 0.0
                def y(self_inner): return 0.0
            return _P()
    C.playground_hover(app._ctx, _Ev())     # must not raise


def test_auto_lut_stays_live_and_reflects_param_changes(playground_app, monkeypatch):
    """Regression: an 'auto' LUT must NOT be overwritten with the first render's
    numbers, so subtle correction-parameter changes (e.g. robust_mesh) keep
    being reflected on screen instead of being masked by a locked window."""
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.image_browser.playground import controller as C
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:128, 0:128]
    img = (300 + 600 * np.exp(-((yy - 40) ** 2 + (xx - 90) ** 2) / (2 * 50 ** 2))
           + rng.normal(0, 40, (128, 128))).astype(np.float32)
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: img)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: None)
    app._playground_bg_cb.setCurrentText("robust_mesh")
    app._playground_radius_edit.setText("100")
    st.source = "corrected"

    app._playground_mesh_sigma_edit.setText("1.0")
    C.playground_on_change(app._ctx)
    a = st.base_rgb.copy()
    # The LUT entries are left as "auto" (live), not clobbered with numbers.
    assert app._playground_lmin_entry.text() == "auto"
    assert app._playground_lmax_entry.text() == "auto"

    app._playground_mesh_sigma_edit.setText("5.0")
    C.playground_on_change(app._ctx)
    assert not np.array_equal(a, st.base_rgb)


def test_on_change_handles_missing_frame(playground_app, monkeypatch):
    app = playground_app
    from well_viewer.features.image_browser.playground import controller as C
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: None)
    C.playground_on_change(app._ctx)        # must not raise
    assert "No raw image" in app._playground_status.text()


def test_ctxbar_threshold_box_reflects_set_value(loaded_app):
    """Regression: the plotting ctxbar threshold box must show the actual
    ThreshFracOn (e.g. after an auto-threshold or Cell Gating edit), not stay at
    its 50.0 default, and follow the active channel."""
    app = loaded_app
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_on_threshold_frac_on_change,
    )
    from well_viewer.core.channel_state_controller import set_active_channel
    set_active_channel(app._ctx, "gfp")
    app._cell_gating_thresh_frac_edits["gfp"].setText("123")
    cell_gating_on_threshold_frac_on_change(app._ctx)
    assert app._plotting_thresh_frac_stepper.value() == 123.0
    app._cell_gating_thresh_frac_edits["mcherry"].setText("77")
    cell_gating_on_threshold_frac_on_change(app._ctx)
    set_active_channel(app._ctx, "mcherry")
    assert app._plotting_thresh_frac_stepper.value() == 77.0


def test_ctxbar_threshold_box_loads_saved_value(viewer_app, mini_dataset):
    """Regression: on load, the ctxbar threshold box must show the threshold
    saved in pipeline_info.json (per active channel), not its 50.0 default."""
    import json
    pinfo = mini_dataset / "pipeline_info.json"
    info = json.loads(pinfo.read_text()) if pinfo.exists() else {}
    info.setdefault("cell_gating", {})["thresh_frac_on"] = {"gfp": 200.0}
    pinfo.write_text(json.dumps(info))
    viewer_app._load_path(mini_dataset)
    assert viewer_app._app_state.channel.active_channel == "gfp"
    assert viewer_app._plotting_thresh_frac_stepper.value() == 200.0


def test_ctxbar_threshold_box_does_not_grab_focus(loaded_app):
    """The threshold box must not auto-grab keyboard focus on show — ClickFocus
    on the stepper and its inner edit so it only focuses on click."""
    from PySide6.QtCore import Qt
    stp = loaded_app._plotting_thresh_frac_stepper
    assert stp.focusPolicy() == Qt.ClickFocus
    edit = getattr(stp, "_edit", None)
    assert edit is not None and edit.focusPolicy() == Qt.ClickFocus


def test_threshold_change_redraws_scope_plots(loaded_app, monkeypatch):
    """Regression: a ThreshFracOn edit must fan out to the fold-change scope
    plots (Bar / Scatter), not just the default-scope plots — otherwise the
    (now default) Bar Plots tab goes stale. The handler emits
    ``plot_options_changed``, which the RedrawCoordinator routes to
    ``redraw_scopes_or_defer``."""
    import well_viewer.domain.fold_change_scopes as fcs
    from well_viewer.core.channel_state_controller import set_thresh_frac_on
    calls = {"n": 0}
    orig = fcs.redraw_scopes_or_defer
    monkeypatch.setattr(
        fcs, "redraw_scopes_or_defer",
        lambda a, *ar, **k: (calls.__setitem__("n", calls["n"] + 1), orig(a, *ar, **k))[1])
    set_thresh_frac_on(loaded_app._ctx, 123.0)
    assert calls["n"] >= 1
