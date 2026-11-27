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
    assert page._run_btn.text() == "Remove from dataset"
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


def _interrupt(src, filt):
    """Leave *src* as an in-place edit cancelled after its first well: one archive cut, the rest as they were."""
    from datasets.subset.apply import execute_plan
    from datasets.subset.subset_compute import SubsetError, build_subset_plan

    plan = build_subset_plan(src, "", filt, in_place=True)
    calls = []
    with pytest.raises(SubsetError, match="Cancelled"):
        execute_plan(plan, is_cancelled=lambda: bool(calls.append(1)) or len(calls) > 1, workers=1)


def test_an_interrupted_in_place_edit_is_finished_from_the_page(qapp, tmp_path):
    """The report: an in-place edit cancelled part-way could not be finished from the page. The page read the
    partly cut archives against the acquisition record, which they no longer match, so its pickers came up empty
    and the run it offered was refused with "Run that same edit again" — while the same marker refused every copy,
    leaving the dataset half-cut with no way on from the app. Wrong implementation: rebuilding the filter from the
    pickers, which cannot offer what the edit recorded."""
    from datasets.subset.subset_compute import IN_PLACE_MARKER, SubsetFilter

    src = _raw(tmp_path / "src")
    _interrupt(src, SubsetFilter(wells=frozenset({"A01", "A02"}), timepoint_labels=frozenset({"48h"})))
    assert (src / IN_PLACE_MARKER).exists()

    ctrl, page = _page_for(src)
    page._in_place_cb.setChecked(True)                  # what a user finishing an in-place edit would tick
    assert page._run_btn.isEnabled()
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert ctrl._last_result[0], ctrl._last_result
    assert sorted(p.name for p in (src / "in").glob("*.zip")) == ["A01.zip", "A02.zip"]
    for well in ("A01", "A02"):
        assert _members(src, well) == _expected(well, timepoints=("48h",))
    assert not (src / IN_PLACE_MARKER).exists()
    assert page.interrupted_edit() is None and page._interrupted_note.isHidden()


def test_the_page_says_what_an_interrupted_edit_keeps_and_offers_only_to_finish_it(qapp, tmp_path):
    from datasets.subset.subset_compute import SubsetFilter

    src = _raw(tmp_path / "src")
    _interrupt(src, SubsetFilter(wells=frozenset({"A01", "A02"}), timepoint_labels=frozenset({"48h"})))
    ctrl, page = _page_for(src)
    assert page.interrupted_edit() is not None
    assert "interrupted in-place edit that keeps wells A01, A02; timepoints 48h" in page._interrupted_note.text()
    assert page._run_btn.text() == "Finish the interrupted edit"
    assert page._run_btn.isEnabled() and not page._queue_btn.isEnabled()
    assert page.in_place() and page._dest_field.isHidden()
    assert page.selected_timepoints() == ["48h"]        # the recorded choice, shown
    assert sorted(page.selected_wells()) == ["A01", "A02"]
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert ctrl._last_result[0], ctrl._last_result
    assert page._run_btn.text() == "Remove from dataset"
    ctrl.shutdown()


def test_finishing_an_interrupted_edit_keeps_what_it_recorded_whatever_the_pickers_say(qapp, tmp_path):
    """Finishing deletes images, so it must delete exactly what the interrupted edit set out to, never what the
    pickers happen to hold. Wrong implementation: a finish that reads the plate and the checkboxes — here it would
    delete A02 and the remaining 24h images, neither of which the recorded edit removes."""
    from datasets.subset.subset_compute import SubsetFilter

    src = _raw(tmp_path / "src")
    _interrupt(src, SubsetFilter(wells=frozenset({"A01", "A02"}), timepoint_labels=frozenset({"24h", "48h"})))
    ctrl, page = _page_for(src)
    page._in_place_cb.setChecked(True)
    # Forced past the lock: a stray signal, or a later change that forgets it, must still not widen the deletion.
    page._plate.setSelectedWellIds(["A01"])
    for label, cb in page._tp_checks.items():
        cb.setChecked(label == "48h")
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert ctrl._last_result[0], ctrl._last_result
    assert sorted(p.name for p in (src / "in").glob("*.zip")) == ["A01.zip", "A02.zip"]
    for well in ("A01", "A02"):
        assert _members(src, well) == _expected(well, timepoints=("24h", "48h"))
    # And the page locks what it shows.
    page._on_source_changed()
    assert page.interrupted_edit() is None              # finished: the controls are the user's again
    _interrupt(src, SubsetFilter(timepoint_labels=frozenset({"48h"})))
    page._on_source_changed()
    assert not page._plate.isEnabled() and not page._in_place_cb.isEnabled()
    assert all(not cb.isEnabled() for cb in page._tp_checks.values())
    ctrl.shutdown()


def test_an_unreadable_interrupted_edit_is_shown_and_offers_no_run(qapp, tmp_path):
    """A marker that cannot be read says an edit was interrupted but not what it kept: nothing may run on it."""
    from datasets.subset.subset_compute import IN_PLACE_MARKER

    src = _raw(tmp_path / "src")
    (src / IN_PLACE_MARKER).write_text("{not json", encoding="utf-8")
    before = {well: _members(src, well) for well in WELLS}
    ctrl, page = _page_for(src)
    assert "can't be read" in page._interrupted_note.text()
    assert not page._run_btn.isEnabled()
    page.runRequested.emit()                            # forced: the controller refuses it too
    assert ctrl._worker is None
    assert {well: _members(src, well) for well in WELLS} == before
    ctrl.shutdown()


def test_a_cancelled_in_place_run_turns_the_page_to_finishing_it(qapp, tmp_path, monkeypatch):
    """Through the real controller and worker: an Edit in place cancelled after one well leaves the page offering
    only to finish that edit, and finishing it from there completes the cut."""
    from datasets.subset.subset_compute import IN_PLACE_MARKER
    from datasets.subset.worker import SubsetWorker

    src = _raw(tmp_path / "src")
    ctrl, page = _page_for(src)
    page._in_place_cb.setChecked(True)
    page._tp_checks["0h"].setChecked(False)
    asked = []
    monkeypatch.setattr(SubsetWorker, "isInterruptionRequested", lambda self: bool(asked.append(1)) or len(asked) > 1)
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert not ctrl._last_result[0] and ctrl._last_result[1].startswith("Cancelled"), ctrl._last_result
    assert (src / IN_PLACE_MARKER).exists()
    assert page._run_btn.text() == "Finish the interrupted edit" and page._run_btn.isEnabled()
    ctrl.shutdown()

    # The next session: the same folder opened afresh, as after a restart.
    monkeypatch.setattr(SubsetWorker, "isInterruptionRequested", lambda self: False)
    ctrl, page = _page_for(src)
    page._in_place_cb.setChecked(True)
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert ctrl._last_result[0], ctrl._last_result
    for well in WELLS:
        assert _members(src, well) == _expected(well, timepoints=("24h", "48h"))
    assert not (src / IN_PLACE_MARKER).exists()
    ctrl.shutdown()
