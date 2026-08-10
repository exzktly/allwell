"""Cell Browser display-control tests: per-channel tint resolution + the
plate-selection per-well-tab allowlist.

These import Qt-pulling modules (worker / selection_controller) but the logic
under test is pure; they run in CI's lane that has PySide6 available.
"""

from __future__ import annotations


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
