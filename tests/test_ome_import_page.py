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


def test_converter_page_readout_and_run_gate(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=12,
                      channel_names=("DAPI", "GFP"))
    ctrl = OmeImportController(on_open_in_analyze=lambda _p: None)
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
