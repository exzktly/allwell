"""The Subset page with a source holding only ``in/``: what it offers, and runs driven through its real controls.

The page is built through ``SubsetController`` (the constructor the Datasets pane calls) and every run goes
through ``runRequested`` and the real worker, so a control that is offered but wired to nothing fails here
(CLAUDE.md §7.6). The archive-level behaviour itself is pinned by ``tests/test_subset_raw.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from services.job_kinds import planned_output
from tests._datasets_worker import run_worker_to_completion
from tests.test_subset_raw import CHANNELS, TPS, WELLS, _expected, _members, _raw


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _page_for(src):
    from datasets.subset.controller import SubsetController

    ctrl = SubsetController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    return ctrl, page


def test_an_in_only_source_is_accepted_and_offers_its_channels_and_schema(qapp, tmp_path):
    src = _raw(tmp_path / "src")
    ctrl, page = _page_for(src)
    assert page._src_field.is_valid()
    assert page.is_raw_source()
    assert not page._raw_box.isHidden()
    assert page.available_channels() == list(CHANNELS)
    assert page.available_timepoints() == list(TPS)
    assert "from the acquisition record" in page._schema_note.text()
    ctrl.shutdown()


def test_an_analysed_source_hides_the_in_only_controls(qapp, tmp_path):
    from tests.fixtures.dataset_builder import build_dataset

    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    ctrl, page = _page_for(src)
    assert not page.is_raw_source()
    assert page._raw_box.isHidden() and page._ch_host.isHidden()
    assert not page.in_place()
    ctrl.shutdown()


def test_a_folder_that_is_no_dataset_says_what_subset_takes(qapp, tmp_path):
    (tmp_path / "empty").mkdir()
    ctrl, page = _page_for(tmp_path / "empty")
    assert not page._src_field.is_valid()
    assert page._src_field.message().startswith("It has no 'in' folder. Subset takes")
    ctrl.shutdown()


def test_typing_a_schema_rereads_the_pickers(qapp, tmp_path):
    def namer(well, tp, field, channel):
        return f"{well}-{tp}-{channel}-{field}.tif"
    src = _raw(tmp_path / "src", record=False, namer=namer)
    ctrl, page = _page_for(src)
    page._schema_edit.setText("well:timepoint:channel:fov")
    page._sep_edit.setText("-")
    page._schema_edit.editingFinished.emit()
    assert "from the schema you typed" in page._schema_note.text()
    assert page.available_fovs() == ["f0", "f1"]
    assert page.available_channels() == list(CHANNELS)
    ctrl.shutdown()


def test_unticking_a_channel_on_the_page_drops_it_from_the_copy(qapp, tmp_path):
    src, dest = _raw(tmp_path / "src"), tmp_path / "dest"
    ctrl, page = _page_for(src)
    page._dest_field.set_path(str(dest))
    page._ch_checks["GFP"].setChecked(False)
    assert page._run_btn.isEnabled()
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert ctrl._last_result[0], ctrl._last_result
    for well in WELLS:
        assert _members(dest, well) == _expected(well, channels=("DAPI",))
    assert page._open_btn.isHidden()            # nothing for Review to open in an unanalysed plate
    ctrl.shutdown()


def test_edit_in_place_needs_no_destination_and_cuts_the_source(qapp, tmp_path):
    src = _raw(tmp_path / "src")
    ctrl, page = _page_for(src)
    page._in_place_cb.setChecked(True)
    assert page._dest_field.isHidden()
    assert not page._in_place_warning.isHidden()
    assert page._run_btn.text() == "▶  Remove from dataset"
    page._tp_checks["48h"].setChecked(False)
    page._plate.setSelectedWellIds(["A01", "A02"])
    assert page._run_btn.isEnabled()                          # no destination asked for
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert ctrl._last_result[0], ctrl._last_result
    assert ctrl._last_result[2] == str(src)
    assert sorted(p.name for p in (src / "in").glob("*.zip")) == ["A01.zip", "A02.zip"]
    assert _members(src, "A01") == _expected("A01", timepoints=("0h", "24h"))
    ctrl.shutdown()


def test_a_queued_in_place_job_round_trips_and_names_its_source_as_output(qapp, tmp_path):
    from datasets.subset.controller import SubsetController

    def namer(well, tp, field, channel):
        return f"{well}-{tp}-{channel}-{field}.tif"
    src = _raw(tmp_path / "src", record=False, namer=namer)
    ctrl, page = _page_for(src)
    page._schema_edit.setText("well:timepoint:channel:fov")
    page._sep_edit.setText("-")
    page._schema_edit.editingFinished.emit()
    page._ch_checks["DAPI"].setChecked(False)
    page._in_place_cb.setChecked(True)
    spec = ctrl.collect_spec()
    assert spec["channels"] == ["GFP"] and spec["in_place"] and spec["destination"] == ""
    assert spec["schema"] == "well:timepoint:channel:fov" and spec["separator"] == "-"
    assert planned_output("subset", spec) == str(src)
    ctrl.shutdown()

    again = SubsetController(on_open_in_review=lambda _p: None)
    again.show_job(spec)
    assert again.collect_spec() == spec
    again.start_from_queue(spec)
    run_worker_to_completion(again)
    assert again._last_result[0], again._last_result
    assert _members(src, "B01") == sorted(namer("B01", t, f, "GFP") for t in TPS for f in ("f0", "f1"))
    again.shutdown()
