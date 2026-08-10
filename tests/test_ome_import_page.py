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
    # The pass/fail line lives in its own label, so a refusal can be red while
    # the neutral "what the file holds" context above it stays muted.
    assert "timepoint(s)" in page._verdict.text()


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

    # Override: mark RFP as phase → it is named as the phase channel but stays a
    # member of the fluor list, because Analyze carries the Phase designation as
    # a checkbox on a channel row and the pipeline quantifies it like any other.
    _, rfp_cb = next((n, cb) for n, cb in page._channel_rows if n == "RFP")
    _select(rfp_cb, "phase")
    assert page.channel_tokens_dict() == {
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP", "RFP"], "phase_token": "RFP"}


def test_channel_roles_prefill_analyze_on_open(qapp, tmp_path):
    # The role assignment reaches Analyze: _open_dataset_in_analyze applies the
    # tokens dict to the Analyze form (nuclear token + fluor rows).
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    tab._apply_pipeline_info({"nuclear_token": "DAPI", "fluor_tokens": ["GFP", "RFP"]})
    assert tab._nuclear_token_edit.text() == "DAPI"
    assert tab._fluor_tokens_list() == ["GFP", "RFP"]


def test_phase_role_handoff_keeps_the_channel_and_ticks_phase(qapp, tmp_path):
    # A channel given the phase role survives the handoff as a row with its Phase
    # box ticked. It used to be dropped from fluor_tokens, which left the Analyze
    # form with no row to carry the flag: the channel vanished and _phase_token()
    # came back empty, silently disabling the phase passthrough.
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    tab._apply_pipeline_info({
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP", "DIC"], "phase_token": "DIC"})
    assert tab._fluor_tokens_list() == ["GFP", "DIC"]
    assert tab._phase_token() == "DIC"


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
    # A preview is a picture of the grid fields, not a second input: it must not
    # reach params, or the grid's own wells become positions 1..n of an order
    # the user is still defining, and every well they then click takes the
    # previous stage position's images.
    assert page.params().well_order == ()

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


# ── how a finished conversion reports itself ─────────────────────────────────
#
# This page has no dashboard — it reports on the acquisition plate it already
# draws — so a run's verdict used to reach only the raw log, which the shared
# log furniture hides by default. A conversion that failed looked exactly like
# one that had not been started.


def _page_with_source(tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=4,
                      channel_names=("DAPI",))
    ctrl = OmeImportController()
    ctrl.page._src_field.set_path(str(p))
    ctrl.page._on_source_changed()
    return ctrl


def test_a_failed_conversion_is_visible_without_opening_the_log(qapp, tmp_path):
    import theme

    ctrl = _page_with_source(tmp_path)
    page = ctrl.page
    assert not page._outcome_label.isVisibleTo(page)      # nothing has run yet

    ctrl._on_failed("plate.ome.tif is incomplete: 2 planes carry no image data.")

    assert page._outcome_label.isVisibleTo(page)
    assert page._outcome_label.text().startswith("Failed — ")
    assert "no image data" in page._outcome_label.text()
    assert theme.Colors.danger in page._outcome_label.styleSheet()
    # The raw log stayed shut: the message has to be readable without it.
    assert not page._raw_log_cb.isChecked()


def test_a_failed_conversion_stops_the_indeterminate_progress_bar(qapp, tmp_path):
    ctrl = _page_with_source(tmp_path)
    page = ctrl.page
    page.set_progress(0, 0)                          # what a launch starts with
    assert page._progress.maximum() == 0             # indeterminate: keeps sweeping

    ctrl._on_failed("boom")

    assert page._progress.maximum() == 1
    assert page._progress.value() == 0


def test_a_cancelled_conversion_reads_as_cancelled_not_failed(qapp, tmp_path):
    import theme

    ctrl = _page_with_source(tmp_path)
    ctrl._on_failed("Cancelled — partial output was left in the destination.")

    text = ctrl.page._outcome_label.text()
    assert theme.Colors.warn in ctrl.page._outcome_label.styleSheet()
    # The worker's message already carries the state word; it is not said twice.
    assert text == "Cancelled — partial output was left in the destination."


def test_a_successful_conversion_says_so_on_the_page(qapp, tmp_path):
    import theme

    ctrl = _page_with_source(tmp_path)
    ctrl._on_succeeded(str(tmp_path / "out"), "Wrote 8 image(s) for 4 well(s)")

    assert ctrl.page._outcome_label.isVisibleTo(ctrl.page)
    assert ctrl.page._outcome_label.text() == "Wrote 8 image(s) for 4 well(s)"
    assert theme.Colors.success in ctrl.page._outcome_label.styleSheet()


def test_a_new_run_drops_the_previous_run_s_verdict(qapp, tmp_path):
    """A second conversion must not begin wearing the first one's failure."""
    ctrl = _page_with_source(tmp_path)
    ctrl._on_failed("boom")
    ctrl.page.clear_outcome()

    assert not ctrl.page._outcome_label.isVisibleTo(ctrl.page)
    assert ctrl.page._outcome_label.text() == ""
    assert ctrl.page._outcome_label.styleSheet() == ""
    assert ctrl.page._outcome_label.toolTip() == ""


# ── which input is in force, and what happens to the other one ───────────────


def _click(page, well_id):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from widgets.well_plate_selector import _parse_well_id

    row, col = _parse_well_id(well_id)
    grid = page._plate._grid
    center = grid._cell_rect(row, col).center()
    grid.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(center), Qt.LeftButton,
        Qt.LeftButton, Qt.NoModifier))


def test_clicking_a_path_does_not_inherit_the_grids_seed_well(qapp, tmp_path):
    """The reported case: a 1×1 grid at B02, then four clicked wells.

    The grid's preview used to be submitted as the acquisition order and clicks
    appended to it, so this produced five wells starting at B02 — every clicked
    well one stage position later than the user meant.
    """
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=4,
                      channel_names=("DAPI",))
    ctrl = OmeImportController()
    page = ctrl.page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    page._rows_edit.setText("1")
    page._cols_edit.setText("1")
    page._origin_edit.setText("B02")
    page.refresh_readout()
    assert page._plate.order() == ["B02"]          # the grid's live preview
    assert page.params().well_order == ()          # …which is not an input

    for well in ("C03", "C04", "D04", "D03"):
        _click(page, well)
    page.refresh_readout()

    assert page.params().well_order == ("C03", "C04", "D04", "D03")
    assert "C03→D03" in page._verdict.text()
    assert "clicked path" in page._verdict.text()


def test_a_hand_drawn_path_greys_out_the_grid_controls(qapp, tmp_path):
    """The user's complaint: fields that still accept input and are ignored."""
    from datasets.ome_import.controller import OmeImportController

    page = OmeImportController().page
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    page.refresh_readout()
    assert page._rows_edit.isEnabled()
    assert not page._grid_note.isVisibleTo(page)

    _click(page, "C03")
    page.refresh_readout()

    for w in (page._rows_edit, page._cols_edit, page._origin_edit,
              page._direction_cb):
        assert not w.isEnabled()
    assert page._grid_note.isVisibleTo(page)
    # FOVs and frame order describe the file, not the ordering — still live.
    assert page._fovs_edit.isEnabled()


def test_clearing_the_path_hands_the_grid_back(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    page = OmeImportController().page
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    page.refresh_readout()
    _click(page, "C03")
    page.refresh_readout()
    assert not page._rows_edit.isEnabled()

    page._plate.clear()
    page.refresh_readout()

    assert page._rows_edit.isEnabled()
    assert not page._grid_note.isVisibleTo(page)
    assert page.params().well_order == ()
    assert page._plate.order() == ["A01", "A02", "B02", "B01"]   # grid again


def test_a_malformed_count_blocks_the_run_and_names_the_field(qapp, tmp_path):
    """`_as_int`-or-zero made "two", "1.5" and "0" mean the same silent auto."""
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=4,
                      channel_names=("DAPI",))
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    page._rows_edit.setText("1")
    page._cols_edit.setText("4")
    page.refresh_readout()
    assert page._run_btn.isEnabled()

    for text, field, expected in (
        ("abc", "_workers_edit", "Workers must be a whole number"),
        ("0", "_fovs_edit", "FOVs per well must be 1 or more"),
        ("two", "_rows_edit", "Rows must be a whole number"),
    ):
        getattr(page, field).setText(text)
        page.refresh_readout()
        assert not page._run_btn.isEnabled(), (field, text)
        assert not page._queue_btn.isEnabled(), (field, text)
        assert expected in page._verdict.text(), (field, text)
        assert page._verdict.objectName() == "Warn"
        getattr(page, field).setText("")


def test_a_refused_plan_is_red_and_a_good_one_is_not(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=4,
                      channel_names=("DAPI",))
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    page._rows_edit.setText("1")
    page._cols_edit.setText("4")
    page.refresh_readout()
    assert page._verdict.objectName() == "Muted"
    assert page._verdict.text().startswith("✓")

    page._cols_edit.setText("3")        # 4 frames don't divide into 3 wells
    page.refresh_readout()
    assert page._verdict.objectName() == "Danger"
    assert page._verdict.text().startswith("⚠")


def test_a_destination_that_already_holds_wells_blocks_the_run(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=4,
                      channel_names=("DAPI",))
    dest = tmp_path / "out_dataset"
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    page._rows_edit.setText("1")
    page._cols_edit.setText("4")
    page.set_destination_path(str(dest))
    page.refresh_readout()

    # A conversion overwrites the wells it writes and keeps the rest, which
    # would leave one folder holding two different plates.
    assert not page._run_btn.isEnabled()


def test_a_run_locks_every_input_that_would_redraw_the_plate(qapp, tmp_path):
    """A grid edit mid-run used to re-seed the preview, so the plate showed
    wells that were not being written and lost the dials for the ones that
    were, while the conversion carried on against the order it started with."""
    from datasets.ome_import.controller import OmeImportController

    page = OmeImportController().page
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    page.refresh_readout()
    page.set_running(True)
    page.set_well_fraction("A01", 1, 2)
    assert page._plate._grid._fractions

    # Enumerated, not listed. A hand-written roster cannot fail when a control
    # is added, and one was: "Use this" arrived a PR later, outside the lock,
    # and clearing the plate through it wiped every dial mid-run.
    from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

    live = []
    for kind in (QComboBox, QLineEdit, QPushButton):
        live += [w for w in page.findChildren(kind)
                 if w.isEnabled() and w.isVisibleTo(page)]
    # Convert and Queue are gated on their own, Open appears only afterwards,
    # and the log's Clear empties a display rather than an input. A PathField's
    # box is read-only and stays readable on purpose while a run goes (it locks
    # its Browse button instead) — that is the one piece of context someone
    # watching a long run most wants to check.
    allowed = {page._run_btn, page._queue_btn, page._open_btn}
    unlocked = [
        w for w in live
        if w not in allowed
        and not (isinstance(w, QLineEdit) and w.isReadOnly())
        and not (isinstance(w, QPushButton) and w.text().strip() == "Clear")
    ]
    assert not unlocked, (
        "these edit the run's inputs and stayed live: "
        + ", ".join(sorted(w.text() or w.objectName() or type(w).__name__
                           for w in unlocked)))
    for w in (page._grid_row, page._fovs_edit, page._seed_btn,
              page._clear_path_btn, page._workers_edit, page._plate,
              page._suggest_btn):
        assert not w.isEnabled(), w

    page._cols_edit.setText("3")        # the keystroke that used to do it
    page.refresh_readout()
    assert page._plate.order() == ["A01", "A02", "B02", "B01"]
    assert page._plate._grid._fractions


# ── the layout the file already knows ────────────────────────────────────────


def _plate_scan(path, rows, cols, fovs):
    """An OME-TIFF carrying real plate stage coordinates in its Plane elements."""
    pitch, fov = 9000.0, 600.0
    coords = []
    for r in range(rows):
        line = list(range(cols))
        if r % 2:
            line.reverse()
        for c in line:
            for f in range(fovs):
                coords.append((c * pitch + (f % 2) * fov, r * pitch + (f // 2) * fov))
    planes = "".join(
        f'<Plane TheT="{i}" TheC="0" TheZ="0" PositionX="{x}" PositionY="{y}"/>'
        for i, (x, y) in enumerate(coords))
    mapping = "".join(
        f'<TiffData FirstT="{i}" FirstC="0" FirstZ="0" IFD="{i}" PlaneCount="1"/>'
        for i in range(len(coords)))
    xml = (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0" Name="scan"><Pixels ID="Pixels:0" '
        'DimensionOrder="XYZCT" Type="uint16" SizeX="6" SizeY="6" SizeC="1" '
        f'SizeZ="1" SizeT="{len(coords)}">'
        '<Channel ID="Channel:0:0" Name="DAPI" SamplesPerPixel="1"/>'
        f'{planes}{mapping}</Pixels></Image></OME>')
    tifffile.imwrite(str(path), np.zeros((len(coords), 6, 6), np.uint16),
                    description=xml, metadata=None, photometric="minisblack")
    return path


def test_the_page_offers_the_layout_the_file_describes(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4)
    page = OmeImportController().page
    assert not page._suggest_row.isVisibleTo(page)

    page._src_field.set_path(str(p))
    page._on_source_changed()

    assert page._suggest_row.isVisibleTo(page)
    assert "2×3 wells × 4 FOV(s)" in page._suggest_label.text()
    # Applied on sight, because the form still held only its defaults: a file
    # that describes itself should not need the same answers typed back in.
    assert (page._rows_edit.text(), page._cols_edit.text()) == ("2", "3")
    assert page._fovs_edit.text() == "4"


def test_an_answered_form_is_never_overwritten_by_the_file(qapp, tmp_path):
    """The metadata beats the defaults it replaces and loses to anything typed
    on purpose — including a grid that happens to disagree with the file."""
    from datasets.ome_import.controller import OmeImportController

    p = _plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4)
    page = OmeImportController().page
    page._rows_edit.setText("3")
    page._cols_edit.setText("8")
    page._src_field.set_path(str(p))
    page._on_source_changed()

    assert (page._rows_edit.text(), page._cols_edit.text()) == ("3", "8")
    assert page._suggest_row.isVisibleTo(page)      # still offered
    page._apply_suggestion()                        # …and applied when asked
    assert (page._rows_edit.text(), page._cols_edit.text()) == ("2", "3")


def test_applying_the_suggestion_fills_the_ordering_controls(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4)
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    page._origin_edit.setText("B02")
    page._apply_suggestion()

    assert (page._rows_edit.text(), page._cols_edit.text()) == ("2", "3")
    assert page._fovs_edit.text() == "4"
    assert page._direction_cb.currentData() == "row"
    # The origin stays the user's, and the plate draws the placed block.
    assert page._plate.order() == ["B02", "B03", "B04", "C04", "C03", "C02"]
    assert "6 well(s) B02→C02" in page._verdict.text()


def test_no_suggestion_is_offered_when_the_file_has_no_stage_coordinates(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "bare.ome.tif", n_frames=6,
                      channel_names=("DAPI",))
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    assert not page._suggest_row.isVisibleTo(page)


def test_the_stage_path_is_drawn_under_the_plate_and_anchored_at_the_origin(
        qapp, tmp_path):
    """The plate can only say which well a position belongs to. Every field of
    view inside one well is that same cell, so the route through them — and how
    far off-centre they sit — has nowhere else on the page to appear."""
    from datasets.ome_import.controller import OmeImportController

    p = _plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4)
    page = OmeImportController().page
    assert not page._path_view.isVisibleTo(page)

    page._src_field.set_path(str(p))
    page._on_source_changed()

    assert page._path_view.isVisibleTo(page)
    assert page._path_caption.isVisibleTo(page)
    points = page._path_view._points
    assert len(points) == 24                      # one dot per image, not per well
    assert points[0] == (0.0, 0.0)                # anchored at A01 by default
    # The four fields of the first well are a fraction of a cell apart.
    assert 0 < points[1][0] - points[0][0] < 0.25

    page._origin_edit.setText("B02")
    page.refresh_readout()
    assert page._path_view._points[0] == (1.0, 1.0)


def test_the_stage_path_follows_a_hand_drawn_first_well(qapp, tmp_path):
    """The anchor is wherever position 1 will actually land, which a clicked
    path decides for itself."""
    from datasets.ome_import.controller import OmeImportController

    p = _plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4)
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()

    _click(page, "D05")
    page.refresh_readout()
    assert page._path_view._points[0] == (4.0, 3.0)      # column 5, row D


def test_no_path_is_drawn_when_the_file_has_no_coordinates(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    p = _write_packed(tmp_path / "bare.ome.tif", n_frames=6,
                      channel_names=("DAPI",))
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    assert not page._path_view.isVisibleTo(page)
    assert not page._path_caption.isVisibleTo(page)


def test_the_stage_inference_runs_once_per_file_not_once_per_keystroke(
        qapp, tmp_path, monkeypatch):
    """Every box on this page recomputes the readout as it is typed in, and the
    inference is a function of the *file*. Left to re-derive itself it ran twice
    per character — on a 96-well timelapse that is 190 ms of UI thread each."""
    import datasets.ome_import.view as view_module
    from datasets.ome_import.controller import OmeImportController

    calls = []
    real = view_module.infer_layout
    monkeypatch.setattr(view_module, "infer_layout",
                        lambda layout: (calls.append(1), real(layout))[1])

    p = _plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4)
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    assert len(calls) == 1

    for text in ("A", "A0", "A03"):
        page._origin_edit.setText(text)
    page._rows_edit.setText("2")
    page._workers_edit.setText("4")
    _click(page, "D05")
    assert len(calls) == 1, "the inference re-ran for a change the file didn't make"
    # …and the drawing still followed every one of those edits.
    assert page._path_view._points[0] == (4.0, 3.0)


def test_the_readout_says_a_run_will_be_resumed(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController
    from datasets.ome_import.ome_import_compute import write_run_marker

    p = _write_packed(tmp_path / "plate.ome.tif", n_frames=6,
                      channel_names=("DAPI",))
    dest = tmp_path / "out"
    page = OmeImportController().page
    page._src_field.set_path(str(p))
    page._on_source_changed()
    page._rows_edit.setText("1")
    page._cols_edit.setText("6")
    page.set_destination_path(str(dest))
    page.refresh_readout()
    assert "Resuming" not in page._readout.text()
    assert page._run_btn.isEnabled()

    # A previous attempt on the same settings left two wells and its marker.
    write_run_marker(dest, p, page.params())
    (dest / "in").mkdir(parents=True, exist_ok=True)
    for well in ("A01", "A02"):
        (dest / "in" / f"{well}.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    page.refresh_readout()

    assert "Resuming an interrupted run: 2 well(s)" in page._readout.text()
    assert page._run_btn.isEnabled()      # a resume is a run, not a refusal

    # Change the grid and the same folder becomes a refusal, not a resume.
    page._cols_edit.setText("3")
    page._rows_edit.setText("2")
    page.refresh_readout()
    assert not page._run_btn.isEnabled()
