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
    from widgets.well_plate_selector import _parse_well_id

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
    # The plate is a WellPlateSelector, so its painted grid child is what
    # handles mouse events.
    grid = page._plate._grid
    center = grid._cell_rect(row, col).center()
    grid.mousePressEvent(QMouseEvent(
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
    run_row_idx = widgets.index(page._run_btn.parentWidget())
    plate_idx = widgets.index(page._plate)
    assert readout_idx < plate_idx
    assert run_row_idx < plate_idx


def test_queue_button_round_trips_source_dest_and_params(qapp, tmp_path):
    """Regression guard: Convert OME-TIFF used to have no "+ Queue" button and
    no QueueableController wiring at all — the only Datasets tool the Job
    Queue couldn't drive. collect_spec()/show_job() must round-trip every
    field params() reads, including the click-defined well order, since
    that's exactly the state a queued run needs to reproduce."""
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=2, channel_names=("DAPI",))
    added = []
    ctrl = OmeImportController(
        on_open_in_analyze=lambda _p: None,
        on_add_to_queue=lambda kind, spec: added.append((kind, spec)),
    )
    page = ctrl.page

    page.set_source_path(str(p))
    page.set_destination_path(str(tmp_path / "dest"))
    page._plate.set_order(["B02", "B03"])
    page.refresh_readout()
    assert page._queue_btn.isEnabled()

    page._queue_btn.click()
    assert len(added) == 1
    kind, spec = added[0]
    assert kind == "ome_import"
    assert spec["source"] == str(p)
    assert spec["destination"] == str(tmp_path / "dest")
    assert spec["params"]["well_order"] == ["B02", "B03"]

    # A fresh controller's page restores from that exact spec.
    ctrl2 = OmeImportController(on_open_in_analyze=lambda _p: None)
    ctrl2.show_job(spec)
    page2 = ctrl2.page
    assert page2.source_path() == spec["source"]
    assert page2.destination_path() == spec["destination"]
    assert page2.params().well_order == ("B02", "B03")


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


def test_conversion_progress_fills_dials_on_the_acquisition_plate(qapp):
    """The page's only progress readout used to be one run-wide bar, with an
    8x12 plate sitting right above it that never changed during a run. The
    plate that defines the visit order is exactly the set of wells being
    written, so it doubles as the progress view — the same thing Subset's
    picker does."""
    from datasets.ome_import.controller import OmeImportController
    from widgets.well_plate_selector import _parse_well_id

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    grid = page._plate._grid

    page.set_well_fraction("A01", 2, 4)
    assert grid._fractions[_parse_well_id("A01")] == 0.5
    page.set_well_fraction("A01", 4, 4)
    assert grid._fractions[_parse_well_id("A01")] == 1.0

    # "Not counted yet" must not render as a definite zero-filled dial.
    page.set_well_fraction("A02", 0, 0)
    assert _parse_well_id("A02") not in grid._fractions

    page.clear_well_fractions()
    assert grid._fractions == {}
    ctrl.shutdown()


def test_progress_bar_is_hidden_until_a_conversion_starts(qapp):
    """An idle bar reading 0% from page load describes a run nobody asked for
    — the same 'reads as a stuck state' problem every other Datasets page's
    status strip was fixed against."""
    from datasets.ome_import.controller import OmeImportController

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    assert page._progress.isHidden()
    page.set_running(True)
    assert not page._progress.isHidden()
    # Stays up afterwards — it is the result, not just the activity.
    page.set_running(False)
    assert not page._progress.isHidden()
    ctrl.shutdown()


def test_the_plate_is_locked_while_a_conversion_runs(qapp):
    """It is the progress display during a run; a click would edit the very
    order being converted."""
    from datasets.ome_import.controller import OmeImportController

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    assert page._plate.isEnabled()
    page.set_running(True)
    assert not page._plate.isEnabled()
    page.set_running(False)
    assert page._plate.isEnabled()
    ctrl.shutdown()


def test_editing_the_order_after_a_run_drops_the_dials(qapp):
    """A dial says how far through *this* order a conversion got, so a
    different order makes it a statement about wells no longer on screen.

    Without this the plate mixes a finished run's fills with a freshly edited
    path: the well the user just removed keeps its full accent disc, because a
    well carrying a fraction is drawn *as* the dial and never reaches the
    depressed branch."""
    from datasets.ome_import.controller import OmeImportController
    from widgets.well_plate_selector import _parse_well_id

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    grid = page._plate._grid

    page._plate.set_order(["A01", "A02", "A03"])
    for well in ("A01", "A02", "A03"):
        page.set_well_fraction(well, 4, 4)
    assert len(grid._fractions) == 3

    # A click removing A02 from the order.
    page._plate.set_order(["A01", "A03"])
    assert grid._fractions == {}
    assert _parse_well_id("A02") not in grid._fractions
    ctrl.shutdown()


def test_a_grid_edit_preview_also_drops_the_dials(qapp):
    """The same applies to the non-dirty serpentine preview a grid edit
    pushes — it is still a different set of wells."""
    from datasets.ome_import.controller import OmeImportController

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    page._plate.set_preview(["A01", "A02"])
    page.set_well_fraction("A01", 1, 1)
    page._plate.set_preview(["A01", "A02", "A03"])
    assert page._plate._grid._fractions == {}
    ctrl.shutdown()


def test_the_acquisition_plate_is_capped_like_every_other_plate(qapp):
    """It sits in a page, not the rail, so it takes the shared caps — an
    uncapped plate grows to the scroll area's full width and pushes the run
    button below the fold, which is why this page used to hardcode 330/210."""
    from datasets.ome_import.controller import OmeImportController
    from widgets.plate_geometry import PLATE_MAX_HEIGHT, PLATE_MAX_WIDTH

    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    plate = ctrl.page._plate
    assert plate.maximumWidth() == PLATE_MAX_WIDTH
    assert plate.maximumHeight() == PLATE_MAX_HEIGHT   # action row hidden
    ctrl.shutdown()
