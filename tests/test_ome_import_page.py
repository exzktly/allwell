"""Headless checks for the Convert OME-TIFF page and the Analyze OME redirect."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402


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


def test_plate_seeds_and_click_defines_well_order(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController
    from widgets.plate_order_editor import _parse_well_id

    ctrl = OmeImportController(on_open_in_analyze=lambda _p: None)
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


def test_page_content_is_wrapped_in_a_scroll_area(qapp):
    """Regression guard: the page used to lay every section directly into
    its own top-level QVBoxLayout with no scroll area, so once the status
    section grew (the log filled with lines, the raw log toggled on), a
    plain fixed-height window had no room left for both it and the
    acquisition-order plate map above — the plate got silently squeezed down
    to a fraction of its natural size instead of the page scrolling to
    reach the status section. set_path() etc. must still resolve on widgets
    reparented under the scroll area's inner content widget — Qt's
    layout.addWidget() reparents regardless of constructor parent."""
    from datasets.ome_import.controller import OmeImportController

    ctrl = OmeImportController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    outer_scroll = page.layout().itemAt(0).widget()
    assert isinstance(outer_scroll, QScrollArea)
    assert outer_scroll.widgetResizable() is True
    assert page._src_field.parent() is not page  # reparented under the scroll content


def test_plate_is_capped_and_sits_below_the_readout_and_convert_button(qapp):
    """Regression guard: PlateOrderEditor carries QSizePolicy.Expanding with no
    cap of its own, so inside the page's scroll area it grew to fill the full
    available width/height -- pushing the readout and Convert button (the
    page's primary action) far below the fold, unlike every other Datasets
    page, which caps its plate map and puts the action above it."""
    from datasets.ome_import.controller import OmeImportController

    ctrl = OmeImportController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page

    assert page._plate.maximumWidth() == 330
    assert page._plate.maximumHeight() == 210

    content = page.layout().itemAt(0).widget().widget()
    outer = content.layout()
    widgets = [outer.itemAt(i).widget() for i in range(outer.count())]
    readout_idx = widgets.index(page._readout)
    run_btn_idx = widgets.index(page._run_btn)
    plate_idx = widgets.index(page._plate)
    assert readout_idx < plate_idx
    assert run_btn_idx < plate_idx


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
