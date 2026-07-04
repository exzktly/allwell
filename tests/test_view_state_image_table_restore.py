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


# ── Fast-lane coverage (no full WellViewerApp → not auto-marked ``slow``) ─────
# These exercise the snapshot/apply helpers against hand-built widgets so the
# round-trip — and the "restore must not generate images on load" contract — run
# in CI's ``-m "not slow"`` lane, not only the manual full pass.

def test_apply_image_table_crop_restore_does_not_generate():
    """Restoring a crop must NOT fire the CropTool's on_change
    (image_table_generate) — otherwise dataset load composites the whole grid
    off disk. Pure: no QApplication needed (CropTool builds no widgets)."""
    from types import SimpleNamespace
    from well_viewer.data.crop_tool import CropTool
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    calls = []
    ct = CropTool(on_change=lambda: calls.append(1))
    app = SimpleNamespace(
        _image_table_crop_tool=ct,
        _image_table_cells=[],
        _image_table_lut={},
        _image_table_row_lut_color_cbs=[],
        _app_state=SimpleNamespace(image_table=ImageTableState()),
    )
    vs._apply_image_table(app, {"crop": [3, 5, 40, 60], "crop_mode": False})
    assert tuple(ct.crop) == (3, 5, 40, 60)
    assert calls == [], "restore must not trigger image_table_generate on load"


def _combo(items, *, data=None):
    from PySide6.QtWidgets import QComboBox
    cb = QComboBox()
    if data is None:
        cb.addItems(items)
    else:
        for label, key in zip(items, data):
            cb.addItem(label, userData=key)
    return cb


def test_channel_and_source_round_trip_minimal_widgets(qapp):
    """Channel (per-cell combo) and Source (per-row combo) round-trip through
    snapshot → apply with hand-built widgets — fast-lane coverage of the path
    Saved Plots replays, without constructing the full app."""
    from types import SimpleNamespace
    from PySide6.QtWidgets import QCheckBox, QLineEdit
    from well_viewer.data.crop_tool import CropTool
    from well_viewer.features.image_browser.image_table.state import ImageTableState
    from widgets.lut_selector import LutSelector

    cell = {
        "chan_cb": _combo(["GFP", "mCherry"]),
        "well_cb": _combo(["A01"]),
        "tp_cb": _combo(["0"]),
        "fov_cb": _combo(["1"]),
    }
    lut0 = {
        "min": QLineEdit(), "max": QLineEdit(), "auto": True,
        "source": _combo(["Raw", "Corrected"], data=["raw", "corrected"]),
        "boundaries": QCheckBox(),
        "binary": _combo(["None", "Mask"], data=["", "mask"]),
        "log": QCheckBox(), "chan": "GFP",
    }
    app = SimpleNamespace(
        _image_table_cells=[[cell]],
        _image_table_lut={0: lut0},
        _image_table_row_lut_color_cbs=[LutSelector()],
        _image_table_crop_tool=CropTool(on_change=lambda: None),
        _app_state=SimpleNamespace(image_table=ImageTableState(rows=1, cols=1)),
    )

    cell["chan_cb"].setCurrentText("mCherry")
    lut0["source"].setCurrentIndex(1)  # Corrected

    snap = vs._snapshot_image_table(app)
    assert snap["cells"][0][0]["chan"] == "mCherry"
    assert snap["row_luts"]["0"]["source"] == "corrected"

    # Wipe, then restore through the apply helper.
    cell["chan_cb"].setCurrentText("GFP")
    lut0["source"].setCurrentIndex(0)
    vs._apply_image_table(app, snap)

    assert cell["chan_cb"].currentText() == "mCherry"
    assert lut0["source"].currentData() == "corrected"


def _set_combo_by_data(cb, data) -> bool:
    for i in range(cb.count()):
        if cb.itemData(i) == data:
            cb.setCurrentIndex(i)
            return True
    return False


def test_image_table_display_state_round_trips(loaded_app):
    app = loaded_app
    C.image_table_repopulate_dropdowns(app._ctx)
    # Build the selector grid + per-row LUT/colormap rows (no render).
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
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


def test_channel_and_source_restore_through_full_view_state_path(loaded_app):
    """Channel (per-cell) and Source (per-row) come back through the SAME path
    Saved Plots uses: full ``view_state.snapshot`` → ``view_state.restore`` →
    ``apply_pending_tab_state`` (rather than calling ``_apply_image_table``
    directly). This guards the build-order/axis gap the user hit."""
    app = loaded_app
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    cells = getattr(app, "_image_table_cells", None) or []
    luts = getattr(app, "_image_table_lut", None) or {}
    if not cells or not cells[0] or 0 not in luts:
        pytest.skip("Image Table grid not built for this dataset")

    chan_cb = cells[0][0].get("chan_cb")
    chans = [chan_cb.itemText(i) for i in range(chan_cb.count()) if chan_cb.itemText(i)]
    if len(chans) < 2:
        pytest.skip("dataset has <2 channels")
    want_chan = chans[1]
    chan_cb.setCurrentText(want_chan)
    if not _set_combo_by_data(luts[0]["source"], "corrected"):
        pytest.skip("no Corrected source option")

    snap = vs.snapshot(app._ctx)
    it = (snap.get("tabs") or {}).get("Image Table") or {}
    assert it.get("cells", [[{}]])[0][0].get("chan") == want_chan
    assert it["row_luts"]["0"].get("source") == "corrected"

    # New session: wipe both, then replay through the public restore path.
    chan_cb.setCurrentText(chans[0])
    _set_combo_by_data(luts[0]["source"], "raw")
    vs.restore(app._ctx, snap)
    # restore stashes pending + tries to activate the tab; apply explicitly so
    # the assertion doesn't depend on notebook/subtab navigation in the harness.
    vs.apply_pending_tab_state(app, "Image Table")

    assert app._image_table_cells[0][0]["chan_cb"].currentText() == want_chan
    assert app._image_table_lut[0]["source"].currentData() == "corrected"
