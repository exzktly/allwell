"""Cell Browser display-control tests: per-channel tint resolution + the
plate-selection per-well-tab allowlist.

These import Qt-pulling modules (worker / selection_controller) but the logic
under test is pure; they run in CI's lane that has PySide6 available.
"""

from __future__ import annotations


def test_resolve_channel_tint_override_and_default():
    from well_viewer.features.image_browser.cell_browser.worker import (
        _CHANNEL_TINTS,
        resolve_channel_tint,
    )

    # Explicit per-channel override (0-255) wins and is normalized to 0..1,
    # case-insensitively keyed.
    assert resolve_channel_tint("GFP", 0, {"gfp": (255, 0, 0)}) == (1.0, 0.0, 0.0)

    # No override → fixed palette by channel index.
    assert resolve_channel_tint("gfp", 0, {}) == _CHANNEL_TINTS[0]
    assert resolve_channel_tint("rfp", 1, {}) == _CHANNEL_TINTS[1]
    assert resolve_channel_tint("anything", 0, None) == _CHANNEL_TINTS[0]  # None map

    # Index wraps around the palette.
    n = len(_CHANNEL_TINTS)
    assert resolve_channel_tint("c", n, {}) == _CHANNEL_TINTS[0]


def test_per_well_tabs_includes_cell_browser():
    from well_viewer.features.registry import build_feature_registry

    # Cell Browser must allow per-well plate selection even with replicate sets;
    # the per-well behaviour now lives on each feature's descriptor
    # (``per_well_selection``) rather than a hard-coded tab-name set.
    reg = build_feature_registry()
    per_well = {d.name for d in reg.all() if d.per_well_selection}
    assert "Cell Browser" in per_well
    # Home carries the sample-definition editors, which pick wells one at a time.
    assert "Home" in per_well
    assert "Review CSV" in per_well
