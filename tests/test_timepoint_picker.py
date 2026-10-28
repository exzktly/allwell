"""The converters' timepoint picker, driven on the real pages.

Behaviour, not layout: every test ticks or unticks a timepoint the way the
picker's menu does (``_set_checked`` is what each menu checkbox calls) and
asserts what reached the params a run uses, the readout, the Run gate, or the
archives the run wrote. Asserting the picker is *offered* would pass against a
dropdown wired to nothing (CLAUDE.md §7.6).
"""

from __future__ import annotations

import os
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settle(page, timeout_s: float = 20.0):
    deadline = time.monotonic() + timeout_s
    while page.is_scanning() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    assert not page.is_scanning(), "the source scan never finished"
    return page


def _untick(picker, token):
    """What unticking that row of the picker's menu does."""
    picker._set_checked(list(picker._data).index(token), False)


# ── the widget's own meaning ─────────────────────────────────────────────────

def test_everything_ticked_is_no_selection_and_nothing_ticked_is_none(qapp):
    from datasets.core.timepoint_picker import TimepointPicker

    picker = TimepointPicker()
    try:
        picker.set_source_tokens(["t0", "t1", "t2"])
        assert picker.subset() == ()
        assert picker.checkedData() == ["t0", "t1", "t2"]

        _untick(picker, "t1")
        assert picker.subset() == ("t0", "t2")

        picker._set_checked(1, True)
        assert picker.subset() == (), "re-ticking the last one is 'all' again"

        picker._set_all(False)
        assert picker.subset() is None
    finally:
        picker.deleteLater()


def test_a_restored_token_the_source_lacks_is_listed_and_all_clears_it(qapp):
    """Dropping it silently would convert a set the job never asked for; and
    the menu's All must lead out of the refusal even before the source's own
    tokens have been listed — else All re-ticks the missing token and None is
    refused, which is a dead end."""
    from datasets.core.timepoint_picker import TimepointPicker

    picker = TimepointPicker()
    try:
        picker.set_subset(("t9",))
        assert picker.subset() == ("t9",)
        picker._set_all(True)
        assert picker.subset() == ()

        picker.set_subset(("t1", "t9"))
        picker.set_source_tokens(["t0", "t1"])
        assert picker.checkedData() == ["t1", "t9"]
        assert "t9 (not in this source)" in picker._labels
        assert picker.subset() == ("t1", "t9")
    finally:
        picker.deleteLater()


# ── ND2: the whole path, picker to archive ───────────────────────────────────

@pytest.fixture
def nd2_controller(qapp):
    pytest.importorskip("nd2")
    from datasets.nd2_import.controller import Nd2ImportController

    ctrl = Nd2ImportController(on_open_in_analyze=lambda *_a: None,
                               on_add_to_queue=lambda *_a: None)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def _nd2_page(ctrl, tmp_path, n_t=3):
    from tests._nd2_fixture import write_nd2

    src = write_nd2(tmp_path / "Plate7.nd2", n_pos=2, n_t=n_t, n_c=1,
                    well_names=["C07", "A02"], channel_names=["DAPI"])
    page = ctrl.page
    page.set_source_path(str(src))
    page.set_destination_path(str(tmp_path / "dest"))
    return _settle(page)


def test_the_picker_lists_the_file_s_timepoints_and_its_ticks_reach_the_run(
        nd2_controller, tmp_path):
    page = _nd2_page(nd2_controller, tmp_path, n_t=3)
    picker = page._timepoint_pick
    assert picker.source_tokens() == ["t0", "t1", "t2"]
    assert page.params().timepoint_subset == ()

    _untick(picker, "t1")
    QApplication.processEvents()

    assert page.params().timepoint_subset == ("t0", "t2")
    assert "2 of 3 timepoint(s) (t0, t2)" in page._verdict.text()
    assert page._run_btn.isEnabled()

    nd2_controller._launch()
    nd2_controller._worker.wait(60_000)
    QApplication.processEvents()
    with zipfile.ZipFile(tmp_path / "dest" / "in" / "C07.zip") as archive:
        assert sorted(archive.namelist()) == ["Plate7_DAPI_C07_f0_t0.tif",
                                              "Plate7_DAPI_C07_f0_t2.tif"]


def test_nothing_ticked_turns_convert_off_with_a_reason(nd2_controller,
                                                        tmp_path):
    page = _nd2_page(nd2_controller, tmp_path)
    page._timepoint_pick._set_all(False)
    QApplication.processEvents()

    assert "Tick at least one timepoint" in page._verdict.text()
    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled()


def test_the_selection_survives_the_job_queue_round_trip(nd2_controller,
                                                         tmp_path):
    from datasets.nd2_import.controller import Nd2ImportController

    page = _nd2_page(nd2_controller, tmp_path)
    _untick(page._timepoint_pick, "t0")
    spec = nd2_controller.collect_spec()
    assert spec["params"]["timepoint_subset"] in (["t1", "t2"], ("t1", "t2"))

    other = Nd2ImportController()
    try:
        other.show_job(spec)
        _settle(other.page)
        assert other.page.params().timepoint_subset == ("t1", "t2")
        assert other.page._timepoint_pick.checkedData() == ["t1", "t2"]
        assert other.collect_spec() == spec
    finally:
        other.shutdown()
        other.page.deleteLater()


def test_the_picker_is_locked_while_a_run_is_in_flight(nd2_controller,
                                                       tmp_path):
    page = _nd2_page(nd2_controller, tmp_path)
    page.set_running(True)
    try:
        assert not page._timepoint_pick.isEnabled()
    finally:
        page.set_running(False)
    assert page._timepoint_pick.isEnabled()


# ── CZI and OME-TIFF: the ticks reach the params and the readout ─────────────

def test_the_czi_picker_reaches_the_params_and_the_readout(qapp, tmp_path):
    from datasets.czi_import.controller import CziImportController
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

    src = write_czi(
        tmp_path / "Plate7.czi",
        plate_tiles(wells=2, fovs=1, timepoints=4, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "C07", 1: "A02"},
                               channels=["DAPI"]))
    ctrl = CziImportController(on_open_in_analyze=lambda *_a: None,
                               on_add_to_queue=lambda *_a: None)
    page = ctrl.page
    try:
        page.set_source_path(str(src))
        page.set_destination_path(str(tmp_path / "dest"))
        _settle(page)
        picker = page._timepoint_pick
        assert picker.source_tokens() == ["t0", "t1", "t2", "t3"]

        _untick(picker, "t0")
        _untick(picker, "t2")
        QApplication.processEvents()

        assert page.params().timepoint_subset == ("t1", "t3")
        assert "2 of 4 timepoint(s) (t1, t3)" in page._verdict.text()
    finally:
        ctrl.shutdown()
        page.deleteLater()


def test_the_ome_picker_reaches_the_params_and_the_readout(qapp, tmp_path):
    from datasets.ome_import.controller import OmeImportController

    path = tmp_path / "plate.ome.tif"
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for _pos in range(2):
            tw.write(np.zeros((5, 1, 6, 6), np.uint16), photometric="minisblack",
                     metadata={"axes": "TCYX", "Channel": {"Name": ["DAPI"]}})
    ctrl = OmeImportController(on_open_in_analyze=lambda *_a: None)
    page = ctrl.page
    try:
        page._src_field.set_path(str(path))
        page._on_source_changed()
        page._rows_edit.setText("1")
        page._cols_edit.setText("2")
        page.refresh_readout()
        picker = page._timepoint_pick
        assert picker.source_tokens() == ["t0", "t1", "t2", "t3", "t4"]

        for token in ("t0", "t2", "t3"):
            _untick(picker, token)
        page._stride_edit.setText("1")

        assert page.params().timepoint_subset == ("t1", "t4")
        assert "2 of 5 timepoint(s) (t1, t4)" in page._verdict.text()
        page.set_running(True)
        assert not picker.isEnabled()
        page.set_running(False)
    finally:
        ctrl.shutdown()
        page.deleteLater()
