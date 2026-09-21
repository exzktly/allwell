"""Headless checks for the Convert CZI page, its controller and its wiring.

Two rules from CLAUDE.md §7.6/§7.8 shape what is here. Every case goes through
the **real** ``CziImportController``, which builds the real page — a page whose
builder raises is a tab that renders blank and a lane that stays green. And the
controls are *driven* rather than inspected: selecting the grid well-source
through the combo, not calling the slot it happens to be connected to, because
"a control is offered" and "a control works" are the two things a Qt callback
bug sits between.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests._czi_fixture import (  # noqa: E402
    plate_metadata_xml,
    plate_tiles,
    write_czi,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp):
    """The real controller, torn down after each test."""
    from datasets.czi_import.controller import CziImportController

    ctrl = CziImportController(on_open_in_analyze=lambda *_a: None,
                               on_add_to_queue=lambda *_a: None)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def _named_czi(tmp_path, wells=("C07", "A02"), channels=("DAPI", "GFP"),
               fovs=1, timepoints=1):
    tiles = plate_tiles(wells=len(wells), fovs=fovs, timepoints=timepoints,
                        channels=len(channels))
    return write_czi(
        tmp_path / "Plate7.czi", tiles,
        xml=plate_metadata_xml(
            scene_wells={i: w for i, w in enumerate(wells)},
            channels=list(channels)),
    )


def _bare_czi(tmp_path, positions=4):
    return write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=positions, fovs=1, timepoints=1,
                                 channels=1))


def _set_source(page, path, tmp_path):
    page.set_source_path(str(path))
    page.set_destination_path(str(tmp_path / "dest"))


# ── the page builds, and its controls do something ───────────────────────────

def test_the_real_page_builds_and_carries_its_controls(controller):
    """The builder is what the app calls; a stubbed seam underneath it would
    never notice a missing argument or a renamed helper in this function."""
    page = controller.page
    labels = [w.text() for w in page.findChildren(type(page._readout))]

    assert "Convert CZI" in labels
    assert page.well_source() == "file"
    assert page._grid_host.isVisibleTo(page) is False


def test_choosing_the_grid_through_the_combo_reveals_the_grid_fields(controller):
    """Driven through ``setCurrentIndex``, so the ``currentIndexChanged``
    connection is part of what is asserted. Calling ``_on_well_source_changed``
    directly would pass against a combo wired to nothing at all."""
    page = controller.page
    index = page._well_source_cb.findData("grid")
    assert index >= 0

    page._well_source_cb.setCurrentIndex(index)

    assert page.well_source() == "grid"
    assert page._grid_host.isVisibleTo(page) is True
    assert page.params().well_source == "grid"


# ── the readout ──────────────────────────────────────────────────────────────

def test_a_named_plate_reads_its_wells_out_of_the_file(controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path), tmp_path)

    assert "2 scene(s)" in page._readout.text()
    assert "wells named by the file (2)" in page._readout.text()
    verdict = page._verdict.text()
    assert "C07→A02" in verdict            # scene order, not plate order
    assert "named by the file" in verdict
    assert page._run_btn.isEnabled()


def test_an_unnamed_plate_on_the_file_source_refuses_and_says_where_to_go(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path), tmp_path)

    assert "Switch the well source to the plate grid" in page._verdict.text()
    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled()


def test_the_grid_fields_turn_an_unnamed_plate_into_a_runnable_plan(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path, positions=4), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")

    assert "A01→B01" in page._verdict.text()   # 2×2 serpentine ends at B01
    assert "from the plate grid" in page._verdict.text()
    assert page._run_btn.isEnabled()


def test_a_grid_that_does_not_divide_the_file_blocks_the_run(controller,
                                                             tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path, positions=5), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")

    assert "not divisible by the well grid" in page._verdict.text()
    assert not page._run_btn.isEnabled()


def test_the_plate_shows_the_mapping_before_the_run(controller, tmp_path):
    """The page's whole answer to "did it read my wells right?". Asserted as
    the wells themselves, not as a count: a plate showing *two* wells that are
    the wrong two would satisfy any count."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("H11", "B05")), tmp_path)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]


def test_an_unreadable_source_says_so_rather_than_showing_a_stale_plan(
        controller, tmp_path):
    page = controller.page
    broken = tmp_path / "broken.czi"
    broken.write_bytes(b"II*\0" + b"\0" * 200)
    _set_source(page, broken, tmp_path)

    assert "not a CZI file" in page._verdict.text()
    assert page._readout.text() == ""
    assert sorted(page._dashboard._fraction) == []
    assert not page._run_btn.isEnabled()


def test_an_incomplete_file_is_refused_on_the_page_not_at_run_time(
        controller, tmp_path):
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles,
                     xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                                            channels=["DAPI", "GFP"]))
    page = controller.page
    _set_source(page, path, tmp_path)

    assert "incomplete" in page._verdict.text()
    assert not page._run_btn.isEnabled()


# ── channels ─────────────────────────────────────────────────────────────────

def test_channel_roles_are_seeded_from_the_names_and_handed_to_analyze(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path, channels=("DAPI", "GFP")), tmp_path)

    assert [name for name, _cb in page._channel_rows] == ["DAPI", "GFP"]
    # DAPI seeds as the nuclear channel, which takes it *out* of
    # ``fluor_tokens`` (``util.channel_roles.channel_tokens_from_roles``: the
    # nuclear channel leaves, a phase one stays). A phase-less plate carries no
    # phase token at all. Asserted as the exact dict Analyze receives rather
    # than as "a dict was produced", which an empty one also satisfies.
    assert page.channel_tokens_dict() == {"nuclear_token": "DAPI",
                                          "fluor_tokens": ["GFP"]}


def test_changing_a_role_changes_what_analyze_is_handed(controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path, channels=("DAPI", "GFP")), tmp_path)
    _name, combo = page._channel_rows[1]

    combo.setCurrentIndex(combo.findData("phase"))

    assert page.channel_tokens_dict()["phase_token"] == "GFP"


# ── the queue hand-off ───────────────────────────────────────────────────────

def test_the_spec_a_queued_job_carries_round_trips_into_a_fresh_page(
        controller, qapp, tmp_path):
    """``collect_spec`` → ``show_job`` is what the Job Queue's edit session
    does. A field that survives the first and not the second is a setting the
    user edits and the run ignores.

    Into a **second, untouched** controller rather than back into the same
    page. Wiping the first page by calling ``set_params`` with the defaults
    looked equivalent and was not: ``origin`` defaults to ``"A01"``, so a
    ``set_params`` that skipped the origin line left ``B03`` sitting in the
    field from before and the round-trip compared a value that had never been
    reset. Mutating that line away left this test green.
    """
    from datasets.czi_import.controller import CziImportController

    page = controller.page
    _set_source(page, _bare_czi(tmp_path), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    page._origin_edit.setText("B03")
    page._serpentine_cb.setChecked(False)
    page._direction_cb.setCurrentIndex(page._direction_cb.findData("col"))
    page._experiment_edit.setText("Run9")
    page._workers_edit.setText("3")
    spec = controller.collect_spec()

    assert spec["params"]["origin"] == "B03"
    assert spec["params"]["serpentine"] is False
    assert spec["params"]["direction"] == "col"
    assert spec["params"]["workers"] == 3
    assert spec["params"]["experiment"] == "Run9"

    other = CziImportController()
    try:
        assert other.collect_spec() != spec         # it really is untouched
        other.show_job(spec)
        assert other.collect_spec() == spec
        assert other.page._origin_edit.text() == "B03"
    finally:
        other.shutdown()
        other.page.deleteLater()


def test_queueing_sends_this_tool_s_own_kind(qapp, tmp_path):
    from datasets.czi_import.controller import CziImportController

    sent: list[tuple[str, dict]] = []
    ctrl = CziImportController(on_add_to_queue=lambda kind, spec:
                               sent.append((kind, spec)))
    try:
        ctrl.page.queueRequested.emit()
        assert [kind for kind, _spec in sent] == ["czi_import"]
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_a_launch_with_no_destination_still_reports_the_job_as_finished(
        controller, tmp_path):
    """The queue is waiting on ``jobFinished`` and nothing else will send one;
    without it the whole queue stops behind this job for the session."""
    finished: list[tuple[bool, str, str]] = []
    controller.jobFinished.connect(lambda *args: finished.append(args))
    controller.page.set_source_path(str(_named_czi(tmp_path)))

    controller._on_run()

    assert finished == [(False, "No destination folder selected.", "")]
    assert controller.page._dashboard.outcome() == "failed"


# ── a whole run, through the controller ──────────────────────────────────────

def test_a_run_converts_the_plate_and_offers_it_to_analyze(controller,
                                                           tmp_path):
    """End to end on the real worker: the page reports done, the archives are
    on disk under the file's own well names, and Open in Analyze appears."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)

    controller._launch()
    assert controller._worker is not None
    controller._worker.wait(30_000)
    QApplication.processEvents()

    dest = tmp_path / "dest"
    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]
    assert page._dashboard.outcome() == "done"
    assert page._open_btn.isVisible() or page._dest_root == str(dest)
    assert not (dest / ".aw_czi_in_progress").exists()


def test_a_second_run_into_the_same_folder_is_refused_rather_than_merged(
        controller, tmp_path):
    """Two plates in one folder, with nothing in it saying so, is the failure
    the destination check exists for — and the ``out/`` measurements beside
    them would then describe inputs that are no longer there."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)
    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    _set_source(page, _named_czi(tmp_path, wells=("H11", "B05")), tmp_path)
    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    assert page._dashboard.outcome() == "failed"
    assert "Already holds converted wells" in page._dashboard.headline_text()
    assert sorted(p.name for p in (tmp_path / "dest" / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]


def test_analyze_redirects_a_raw_czi_to_this_page(qapp, tmp_path):
    """Analyze processes folders, so a `.czi` dropped into its input field has
    to send the user here. Without it the refusal reads "Not a directory",
    which names neither the problem nor the page that solves it."""
    from analyze.tab import AnalyzeTab

    path = _named_czi(tmp_path)
    tab = AnalyzeTab()
    try:
        tab._input_edit.setText(str(path))
        hint = tab._packed_input_hint()

        assert hint is not None
        assert "Convert CZI" in hint
        # Not the other converter's page: it would refuse the file.
        assert "Convert OME-TIFF" not in hint
        assert tab._output_lbl.text() == "—"
        assert "Convert CZI" in tab._layout_lbl.text()
    finally:
        tab.deleteLater()
