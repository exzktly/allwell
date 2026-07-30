"""Headless checks for the Convert OME-TIFF page and the Analyze OME redirect."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_packed(path, *, n_frames, channel_names):
    data = np.zeros((n_frames, len(channel_names), 6, 6), np.uint16)
    tifffile.imwrite(
        str(path), data, ome=True, photometric="minisblack",
        metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}},
    )
    return path


def _write_multi(path, *, positions, channel_names):
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for _ in range(positions):
            arr = np.zeros((1, len(channel_names), 6, 6), np.uint16)
            tw.write(arr, metadata={"axes": "TCYX",
                                    "Channel": {"Name": list(channel_names)}})
    return path


def test_converter_page_readout_and_run_gate(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=12,
                      channel_names=("DAPI", "GFP"))
    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page

    # No source yet → Convert disabled, frame-order hidden.
    assert not page._run_btn.isEnabled()

    page._src_field.set_path(str(p))
    page._on_source_changed()  # simulates the field's changed signal
    # Single-image packed → frame-order selector visible, destination auto-filled.
    assert page._frame_order_cb.isVisibleTo(page)
    assert page.destination_path().endswith("plate_dataset")

    # 1×3 grid, 2 FOVs, single timepoint would need 6 frames (have 12) → blocked.
    page._rows_edit.setText("1")
    page._cols_edit.setText("3")
    page._fovs_edit.setText("2")
    _select(page._frame_order_cb, "single")
    page.refresh_readout()
    assert not page._run_btn.isEnabled()

    # positions-within-timepoint splits 12 into 6 positions × 2 timepoints.
    _select(page._frame_order_cb, "pos_in_time")
    page.refresh_readout()
    assert page._run_btn.isEnabled()
    params = page.params()
    assert params.frame_order == "pos_in_time"
    assert params.fovs_per_well == 2
    assert "timepoint(s)" in page._readout.text()


def test_readout_shows_metadata_well_target(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    # 6-position multi-image file: the readout should surface the target the
    # moment the source is picked, before any grid is entered.
    p = _write_multi(tmp_path / "m.ome.tif", positions=6, channel_names=("GFP",))
    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    assert "6 stage position(s)" in page._readout.text()

    # Pinning FOVs turns the budget into an explicit expected-well count.
    page._fovs_edit.setText("2")
    page.refresh_readout()
    assert "3 well(s) at 2 FOV(s)/well" in page._readout.text()


def test_channel_roles_seed_from_names_and_export_tokens(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_multi(tmp_path / "m.ome.tif", positions=2,
                     channel_names=("DAPI", "GFP", "RFP"))
    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    page._src_field.set_path(str(p))
    page._on_source_changed()

    # One role row per channel, seeded by the name heuristic.
    assert [name for name, _cb in page._channel_rows] == ["DAPI", "GFP", "RFP"]
    assert page._channels_caption.isVisibleTo(page)
    assert page.channel_tokens_dict() == {
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP", "RFP"]}

    # Override: mark RFP as phase → it leaves the fluor list.
    _, rfp_cb = next((n, cb) for n, cb in page._channel_rows if n == "RFP")
    _select(rfp_cb, "phase")
    assert page.channel_tokens_dict() == {
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP"], "phase_token": "RFP"}


def test_channel_roles_prefill_analyze_on_open(qapp, tmp_path):
    # The role assignment reaches Analyze: _open_dataset_in_analyze applies the
    # tokens dict to the Analyze form (nuclear token + fluor rows).
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    tab._apply_pipeline_info({"nuclear_token": "DAPI", "fluor_tokens": ["GFP", "RFP"]})
    assert tab._nuclear_token_edit.text() == "DAPI"
    assert tab._fluor_tokens_list() == ["GFP", "RFP"]


def test_plate_seeds_and_click_defines_well_order(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController
    from widgets.plate_order_editor import _parse_well_id

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page

    # A valid grid drives a live (non-dirty) serpentine preview on the plate.
    page._rows_edit.setText("1")
    page._cols_edit.setText("3")
    page.refresh_readout()
    assert page._plate.order() == ["A01", "A02", "A03"]
    assert not page._plate.is_dirty()
    # A preview does not populate params.well_order until the user owns it…
    assert page.params().well_order == ("A01", "A02", "A03")

    # "Generate path from grid" seeds an editable (dirty) order.
    page._generate_path()
    assert page._plate.is_dirty()

    # Clicking a fourth, off-grid well appends it → a non-rectangular order.
    row, col = _parse_well_id("H12")
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    center = page._plate._cell_rect(row, col).center()
    page._plate.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(center), Qt.LeftButton,
        Qt.LeftButton, Qt.NoModifier))
    assert page.params().well_order == ("A01", "A02", "A03", "H12")

    # A later grid edit no longer overwrites the hand-drawn order.
    page._cols_edit.setText("2")
    page.refresh_readout()
    assert page.params().well_order == ("A01", "A02", "A03", "H12")


def test_analyze_redirects_a_raw_ome_tiff(qapp, tmp_path):
    from analyze_tab import AnalyzeTab

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=4, channel_names=("GFP",))
    tab = AnalyzeTab()
    tab._input_edit.setText(str(p))
    hint = tab._ome_input_hint()
    assert hint is not None and "Convert OME-TIFF" in hint
    # The output/layout labels carry the redirect, not a run plan.
    assert tab._output_lbl.text() == "—"
    assert "Convert OME-TIFF" in tab._layout_lbl.text()


def _select(combo, value):
    combo.setCurrentIndex(
        [combo.itemData(i) for i in range(combo.count())].index(value))
