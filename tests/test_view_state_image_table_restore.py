"""Regression: the Image Table's display state (crop region, Raw/Corrected,
per-row colormap, and LUT min/max) must round-trip through view_state — which is
exactly what Saved Plots save/restore replays.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.persistence import view_state as vs  # noqa: E402
from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402

pytestmark = pytest.mark.qt


def _set_combo_by_data(cb, data) -> bool:
    for i in range(cb.count()):
        if cb.itemData(i) == data:
            cb.setCurrentIndex(i)
            return True
    return False


def test_image_table_display_state_round_trips(loaded_app):
    app = loaded_app
    C.image_table_repopulate_dropdowns(app)
    # Build the selector grid + per-row LUT/colormap rows (no render).
    C.image_table_apply_preset(app, "channel", "timepoint")
    luts = getattr(app, "_image_table_lut", None) or {}
    color_cbs = getattr(app, "_image_table_row_lut_color_cbs", None) or []
    if not luts or not color_cbs or 0 not in luts:
        pytest.skip("dataset built no Image Table LUT rows")

    # ── set a distinctive display state ──────────────────────────────────────
    app._image_table_crop_tool.set_crop((3, 5, 40, 60))
    row0 = luts[0]
    if not _set_combo_by_data(row0["source"], "corrected"):
        pytest.skip("no Corrected source option")
    color_cbs[0].setLut("magma")
    row0["min"].setText("100")
    row0["max"].setText("2000")

    snap = vs._snapshot_image_table(app)
    assert snap.get("crop") == [3, 5, 40, 60]
    s0 = snap["row_luts"]["0"]
    assert s0.get("source") == "corrected"
    assert s0.get("cmap") == "magma"
    assert s0.get("min") == "100" and s0.get("max") == "2000"

    # ── new session: wipe the state, then restore it ─────────────────────────
    app._image_table_crop_tool.clear()
    _set_combo_by_data(row0["source"], "raw")
    color_cbs[0].setLut("viridis")
    row0["min"].setText("")
    row0["max"].setText("")

    vs._apply_image_table(app, snap)

    # Dimensions are unchanged, so the live widgets are not rebuilt.
    live0 = app._image_table_lut[0]
    assert tuple(app._image_table_crop_tool.crop) == (3, 5, 40, 60)
    assert live0["source"].currentData() == "corrected"
    assert app._image_table_row_lut_color_cbs[0].lut() == "magma"
    assert live0["min"].text() == "100"
    assert live0["max"].text() == "2000"


def test_snapshot_tolerates_unbuilt_image_table(loaded_app):
    """No grid built yet → snapshot is an empty dict, not an exception."""
    app = loaded_app
    app._image_table_cells = []
    assert vs._snapshot_image_table(app) == {}
