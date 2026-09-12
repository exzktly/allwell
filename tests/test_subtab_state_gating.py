"""A composite parent's sub-tab-owned state slices must drop with the sub-tab.

Image Browser survives as a top-level feature in the thin profile but its sub-tabs
are narrowed to ``["Segmentation", "Image Table"]``. Its ``playground`` state slice
lives on the *parent* descriptor (not on the sub-leaf descriptors, which carry
no state), and its state module is physically stripped from a subset bundle.
Without sub-tab gating, ``attach_feature_state`` imports that stripped module
eagerly in ``ReviewApp.__init__`` and a frozen thin build silently exits.

These lock in the fix: the tagged slices are pruned when the profile drops their
sub-tab (reproducing the frozen "module absent" condition in a dev tree), and kept
when it admits them.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.core.app_state import AppState
from core.profile import FULL, load_profile
from review.features.registry import (
    _features,
    attach_feature_state,
    build_feature_registry,
)


def _image_browser_state_attrs(profile):
    ib = build_feature_registry(profile).get("Image Browser")
    assert ib is not None
    return {s.attr for s in ib.states}


def test_thin_prunes_subtab_owned_slices():
    # thin admits Segmentation + Image Table, so their slices stay; Playground
    # (and Cell Browser) are pruned, so ``playground`` drops.
    assert _image_browser_state_attrs(load_profile("thin")) == {
        "review_image", "preview", "image_table",
    }


def test_full_keeps_all_slices():
    assert _image_browser_state_attrs(FULL) == {
        "review_image", "preview", "image_table", "playground",
    }


def test_attach_skips_pruned_slices_under_thin():
    state = AppState()
    attach_feature_state(state, build_feature_registry(load_profile("thin")))
    # Segmentation + Image Table slices survive; the dropped Playground slice does not.
    assert state.feature_slice("review_image") is not None
    assert state.feature_slice("image_table") is not None
    assert state.feature_slice("playground") is None


def test_subtab_owned_slices_are_tagged():
    """Guard against regression: any Image Browser parent slice that names a
    droppable sub-tab must carry a ``subtab`` tag, else it silently re-breaks a
    subset build."""
    ib = next(d for d in _features() if d.name == "Image Browser")
    tagged = {s.attr: s.subtab for s in ib.states if s.subtab}
    assert tagged == {"image_table": "Image Table", "playground": "Playground"}
