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
    from flatfield_core import normalize_flatfield
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
    from well_viewer.features.playground.renderer import build_playground_rgb
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
    from well_viewer.features.playground.renderer import build_playground_rgb
    arr = np.zeros((8, 8), dtype=np.float32)
    mask = np.zeros((8, 8), dtype=np.int32)
    mask[2:5, 2:5] = 7
    rgb = build_playground_rgb(arr, mask, binary=True)
    assert (rgb[3, 3] == (255, 255, 255)).all()    # inside the cell
    assert (rgb[0, 0] == (0, 0, 0)).all()          # background black


# ── reference-field worker compute ───────────────────────────────────────────

def test_build_reference_fields_estimates(monkeypatch):
    from well_viewer.features.playground import worker
    import well_viewer.features.image_table.controller as itc
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
    from well_viewer.features.playground import worker
    import well_viewer.features.image_table.controller as itc
    monkeypatch.setattr(itc, "_load_well_channel", lambda *a, **k: {})
    ff, bg = worker.build_reference_fields(
        None, "H12", "gfp", flatfield_mode="basic", background_method="empty_well")
    assert ff is None and bg is None


# ── controller orchestration (offscreen app) ─────────────────────────────────

@pytest.fixture
def playground_app(loaded_app):
    """A loaded app with the Playground sub-tab built."""
    from well_viewer.features.playground.tab_view import build_playground_tab
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


def test_on_change_corrects_and_renders(playground_app, monkeypatch):
    app = playground_app
    st = app._app_state.playground
    from well_viewer.features.playground import controller as C
    frame = _frame(5)
    mask = np.zeros_like(frame, dtype=np.int32)
    mask[10:20, 10:20] = 1
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: frame)
    monkeypatch.setattr(C, "_load_mask_array", lambda *a, **k: mask)

    app._playground_bg_cb.setCurrentText("sliding_disk")
    app._playground_radius_edit.setText("12")
    app._playground_source_cb.setCurrentText("Corrected")
    C.playground_on_change(app)
    assert st.base_rgb is not None
    assert app._playground_image_label.pixmap() is not None
    corrected_rgb = st.base_rgb.copy()

    # Raw vs Corrected differ for a non-none method.
    app._playground_source_cb.setCurrentText("Raw")
    C.playground_on_change(app)
    assert not np.array_equal(corrected_rgb, st.base_rgb)


def test_on_change_handles_missing_frame(playground_app, monkeypatch):
    app = playground_app
    from well_viewer.features.playground import controller as C
    monkeypatch.setattr(C, "_load_array", lambda *a, **k: None)
    C.playground_on_change(app)        # must not raise
    assert "No raw image" in app._playground_status.text()
