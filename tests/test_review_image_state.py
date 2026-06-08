"""Pure-Python unit tests for ``ReviewImageState`` and ``PreviewState``.

These are conservative data containers; the tests pin the defaults and the two
cohesive behaviours — view reset and cache invalidation.
"""

from __future__ import annotations

from well_viewer.features.review_image.state import PreviewState, ReviewImageState


# ── ReviewImageState ─────────────────────────────────────────────────────────

def test_review_image_defaults():
    ris = ReviewImageState()
    assert ris.zoom == 1.0
    assert ris.pan_x == 0.0 and ris.pan_y == 0.0
    assert ris.show_outline is True
    assert ris.image_source == "corrected"
    assert ris.flatfield_field_cache == {}
    assert ris.binary_mask is False
    assert ris.boundary_color == (255, 64, 64)
    assert ris.selected_color == (255, 230, 64)
    assert ris.tint_color == (255, 255, 255)
    assert ris.frame_cache is None
    assert ris.selected_nucleus is None


def test_review_image_mutable_defaults_are_per_instance():
    a, b = ReviewImageState(), ReviewImageState()
    a.include_cache["k"] = 1
    a.lut_by_channel["gfp"] = (0.0, 1.0)
    assert b.include_cache == {}
    assert b.lut_by_channel == {}


def test_reset_view():
    ris = ReviewImageState(zoom=3.0, pan_x=120.0, pan_y=-40.0)
    ris.reset_view()
    assert ris.zoom == 1.0
    assert ris.pan_x == 0.0
    assert ris.pan_y == 0.0


def test_clear_caches():
    ris = ReviewImageState(
        frame_cache={"decoded": 1},
        include_cache={"a": {1: True}},
        threshold_map_cache={"b": {2: False}},
    )
    ris.clear_caches()
    assert ris.frame_cache is None
    assert ris.include_cache == {}
    assert ris.threshold_map_cache == {}


# ── PreviewState ─────────────────────────────────────────────────────────────

def test_preview_defaults():
    ps = PreviewState()
    assert ps.selected_well is None
    assert ps.fluor == {}
    assert ps.overlay == {}
    assert ps.mask == {}
    assert ps.montage_photos == []


def test_preview_mutable_defaults_are_per_instance():
    a, b = PreviewState(), PreviewState()
    a.fluor[("1", "0h")] = object()
    a.montage_photos.append(object())
    assert b.fluor == {}
    assert b.montage_photos == []
