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


class _RuntimeNS:
    """Mimics ``AppRuntime._FeatureRuntime``: attributes set at construction
    resolve; any *unset* attribute returns ``None`` (never ``AttributeError``),
    exactly like the real prefix-backed runtime the ctx-native
    ``_apply_image_table`` reads. Lets the fakes below omit the widget handles a
    given restore payload never touches."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, _name):  # only reached for names not in __dict__
        return None


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
    # ctx-shaped fake: the ctx-native ``_apply_image_table`` reads the live grid
    # objects off ``ctx.runtime.image_table`` (None for the handles this payload
    # never touches) and the typed slice off ``ctx.state``.
    ctx = SimpleNamespace(
        state=SimpleNamespace(image_table=ImageTableState()),
        runtime=SimpleNamespace(image_table=_RuntimeNS(
            crop_tool=ct, cells=[], lut={}, row_lut_color_cbs=[],
            row_chan_cbs=None, row_well_cbs=None)),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )
    vs._apply_image_table(ctx, {"crop": [3, 5, 40, 60], "crop_mode": False})
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
    cells = [[cell]]
    lut = {0: lut0}
    color_cbs = [LutSelector()]
    crop = CropTool(on_change=lambda: None)
    it_state = SimpleNamespace(image_table=ImageTableState(rows=1, cols=1))
    # ctx-shaped fake: ``state`` / ``runtime.image_table`` (None for unset grid
    # widgets) / ``widgets`` (+ a ``_ctx`` self-ref) back both the ctx-native
    # ``_snapshot_image_table`` and ``_apply_image_table`` off the same live
    # objects.
    app = SimpleNamespace(
        state=it_state,
        runtime=SimpleNamespace(image_table=_RuntimeNS(
            cells=cells, lut=lut, row_lut_color_cbs=color_cbs, crop_tool=crop,
            row_chan_cbs=None, row_well_cbs=None)),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
    )
    app._ctx = app

    cell["chan_cb"].setCurrentText("mCherry")
    lut0["source"].setCurrentIndex(1)  # Corrected

    snap = vs._snapshot_image_table(app._ctx)
    assert snap["cells"][0][0]["chan"] == "mCherry"
    assert snap["row_luts"]["0"]["source"] == "corrected"

    # Wipe, then restore through the apply helper.
    cell["chan_cb"].setCurrentText("GFP")
    lut0["source"].setCurrentIndex(0)
    vs._apply_image_table(app._ctx, snap)

    assert cell["chan_cb"].currentText() == "mCherry"
    assert lut0["source"].currentData() == "corrected"


def _set_combo_by_data(cb, data) -> bool:
    for i in range(cb.count()):
        if cb.itemData(i) == data:
            cb.setCurrentIndex(i)
            return True
    return False


def test_image_table_display_state_round_trips(image_table_app):
    # image_table_app builds the lazy Image Table sub-tab, so the live grid
    # objects (cells / per-row LUT rows / colour selectors / crop tool) exist on
    # ctx.runtime.image_table — the A8-teardown home the ctx-native snapshot /
    # apply read from (the old ``app._image_table_*`` stamps no longer track it).
    app = image_table_app
    it = app._ctx.runtime.image_table
    C.image_table_repopulate_dropdowns(app._ctx)
    # Build the selector grid + per-row LUT/colormap rows (no render).
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    luts = it.lut or {}
    color_cbs = it.row_lut_color_cbs or []
    assert luts and color_cbs and 0 in luts, "apply_preset built no Image Table LUT rows"

    # ── set a distinctive display state ──────────────────────────────────────
    it.crop_tool.set_crop((3, 5, 40, 60))
    row0 = luts[0]
    assert _set_combo_by_data(row0["source"], "corrected"), "no Corrected source option"
    color_cbs[0].setLut("magma")
    row0["min"].setText("100")
    row0["max"].setText("2000")

    snap = vs._snapshot_image_table(app._ctx)
    assert snap.get("crop") == [3, 5, 40, 60]
    s0 = snap["row_luts"]["0"]
    assert s0.get("source") == "corrected"
    assert s0.get("cmap") == "magma"
    assert s0.get("min") == "100" and s0.get("max") == "2000"

    # ── new session: wipe the state, then restore it ─────────────────────────
    it.crop_tool.clear()
    _set_combo_by_data(row0["source"], "raw")
    color_cbs[0].setLut("viridis")
    row0["min"].setText("")
    row0["max"].setText("")

    vs._apply_image_table(app._ctx, snap)

    # Dimensions are unchanged, so the live widgets are not rebuilt.
    live0 = it.lut[0]
    assert tuple(it.crop_tool.crop) == (3, 5, 40, 60)
    assert live0["source"].currentData() == "corrected"
    assert it.row_lut_color_cbs[0].lut() == "magma"
    assert live0["min"].text() == "100"
    assert live0["max"].text() == "2000"


def test_snapshot_tolerates_unbuilt_image_table(loaded_app):
    """No grid built yet → snapshot is an empty dict, not an exception."""
    app = loaded_app
    app._ctx.runtime.image_table.cells = []
    assert vs._snapshot_image_table(app._ctx) == {}


def test_channel_and_source_restore_through_full_view_state_path(image_table_app):
    """Channel (per-cell) and Source (per-row) come back through the SAME path
    Saved Plots uses: full ``view_state.snapshot`` → ``view_state.restore`` →
    ``apply_pending_tab_state`` (rather than calling ``_apply_image_table``
    directly). This guards the build-order/axis gap the user hit."""
    app = image_table_app
    it = app._ctx.runtime.image_table
    C.image_table_repopulate_dropdowns(app._ctx)
    C.image_table_apply_preset(app._ctx, "channel", "timepoint")
    assert it.cells and it.cells[0] and 0 in (it.lut or {}), \
        "apply_preset built no Image Table grid"

    chan_cb = it.cells[0][0].get("chan_cb")
    chans = [chan_cb.itemText(i) for i in range(chan_cb.count()) if chan_cb.itemText(i)]
    assert len(chans) >= 2, "dataset has <2 channels"
    want_chan = chans[1]
    # Setting a cell channel fires image_table_rebuild_lut_row, which REPLACES
    # ``it.lut`` with a fresh dict — so always read ``it.lut[0]`` afresh after a
    # channel change rather than caching the row's LUT slot.
    chan_cb.setCurrentText(want_chan)
    assert _set_combo_by_data(it.lut[0]["source"], "corrected"), "no Corrected source option"

    snap = vs.snapshot(app._ctx)
    it_snap = (snap.get("tabs") or {}).get("Image Table") or {}
    assert it_snap.get("cells", [[{}]])[0][0].get("chan") == want_chan
    assert it_snap["row_luts"]["0"].get("source") == "corrected"

    # New session: wipe both, then replay through the public restore path.
    chan_cb.setCurrentText(chans[0])
    _set_combo_by_data(it.lut[0]["source"], "raw")
    vs.restore(app._ctx, snap)
    # restore stashes pending + tries to activate the tab; apply explicitly so
    # the assertion doesn't depend on notebook/subtab navigation in the harness.
    vs.apply_pending_tab_state(app._ctx, "Image Table")

    assert it.cells[0][0]["chan_cb"].currentText() == want_chan
    assert it.lut[0]["source"].currentData() == "corrected"
