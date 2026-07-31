"""Offscreen controller/page tests for the Import Loose Folder feature.

Same pattern as ``tests/test_validate_page.py`` / ``tests/test_subset_page.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

SCHEMA = "experiment:channel:well:fov:timepoint"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_loose(folder, wells, timepoints=("0h",)):
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        for tp in timepoints:
            (folder / f"exp_GFP_{well}_F001_{tp}.tif").write_bytes(b"img")


def _run_worker_to_completion(ctrl) -> None:
    worker = ctrl._worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(10):
        QApplication.processEvents()


def test_dashboard_hidden_until_convert_starts(qapp, tmp_path):
    """Regression guard: the status strip below the Convert button used to
    be visible from page load, showing an idle headline over an empty plate
    map before anything had happened. Uses isHidden() (a local, explicit
    hide/show flag) rather than isVisible(), since the page is never
    actually shown in this test — isVisible() would report False for the
    whole tree regardless of these setVisible() calls."""
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page.dashboard.isHidden() is True  # still nothing has run

    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()
    page.runRequested.emit()
    assert page.dashboard.isHidden() is False  # shown the moment Convert starts
    _run_worker_to_completion(ctrl)
    assert page.dashboard.isHidden() is False  # stays up to show the result

    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page.dashboard.isHidden() is True  # hidden again for the new selection
    ctrl.shutdown()


def test_readout_updates_on_source_change(qapp, tmp_path):
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02"], timepoints=("0h", "24h"))
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert "2 well" in page._readout.text()
    ctrl.shutdown()


def test_run_gate_requires_destination_unless_in_place(qapp, tmp_path):
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert not page._run_btn.isEnabled()

    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()

    page._in_place_cb.setChecked(True)
    assert page._run_btn.isEnabled()
    assert page._dest_field.isHidden()
    ctrl.shutdown()


def test_run_gate_refuses_a_destination_that_already_has_content(qapp, tmp_path):
    """Regression guard: the destination PathField had no validator at all
    (unlike Concatenate's/Merge Plates'), and _can_run() only checked
    bool(destination_path()) -- a destination already holding another
    dataset's files was accepted by the run gate, and the actual convert
    would then silently overwrite that dataset's in/<WELL>.zip (see
    tests/test_import_loose_compute.py's apply.py-level regression test)."""
    from tests.fixtures.dataset_builder import build_dataset

    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    existing = tmp_path / "existing"
    build_dataset(existing, wells=["A01"], timepoints=["0h"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    page._dest_field.set_path(str(existing))
    page._refresh_run_enabled()
    assert not page._run_btn.isEnabled()
    assert not page._dest_field.isHidden()
    assert "already has files" in page._dest_field.message().lower()

    page._dest_field.set_path(str(tmp_path / "fresh_dest"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()
    ctrl.shutdown()


def test_convert_to_new_destination_through_the_page(qapp, tmp_path):
    import zipfile

    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    _write_loose(src, ["A01", "B02"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        assert zf.namelist() == ["exp_GFP_A01_F001_0h.tif"]
    assert not page._open_btn.isHidden()
    # The per-well plate map (Analyze-style graphical progress) reflects the
    # real conversion, not just a plain progress bar: both wells the scan
    # found are tracked and land in the "done" state.
    assert page.dashboard._wells == ["A01", "B02"]
    assert page.dashboard._well_stage == {"A01": "done", "B02": "done"}
    assert page.dashboard.overall_maximum() == 2
    ctrl.shutdown()


def test_group_in_place_through_the_page_zips_and_deletes_sources(qapp, tmp_path):
    """Regression for the reported "sits stuck, no zips made" bug (fixed in
    apply.py's convert_in_place/_zip_well_folders) plus the adjacent bug found
    alongside it: the checkbox label ("delete loose TIFs on success") and the
    confirm dialog both promise deletion, but the controller used to hardcode
    move_source_tifs=False regardless of in_place — so WellPlateZipper's
    folder_well() always copied (never moved) originals, leaving them behind
    even on a fully successful run. Drives the real page + worker +
    subprocess-backed run_zipper end to end (stdlib-only, no imaging stack)."""
    import zipfile

    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02"], timepoints=("0h", "24h"))

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    ctrl._confirm_in_place = lambda: True  # bypass the modal QMessageBox
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._in_place_cb.setChecked(True)
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    for well in ("A01", "B02"):
        zip_path = src / f"{well}.zip"
        assert zip_path.exists(), f"{well}: expected a zip, found none"
        with zipfile.ZipFile(zip_path) as zf:
            assert set(zf.namelist()) == {f"exp_GFP_{well}_F001_0h.tif", f"exp_GFP_{well}_F001_24h.tif"}
    assert not list(src.glob("*.tif")), "loose TIF originals should be deleted, not copied, on success"
    assert not page._open_btn.isHidden()
    # In-place mode drives the same plate-map dashboard through the
    # subprocess-backed run_zipper's progress_fn -> "writing" then
    # _zip_well_folders's own "done" report, not just the new-destination
    # mode's ThreadPoolExecutor path.
    assert page.dashboard._wells == ["A01", "B02"]
    assert page.dashboard._well_stage == {"A01": "done", "B02": "done"}
    ctrl.shutdown()


def test_queue_spec_round_trips_filename_schema_and_separator(qapp, tmp_path):
    """Regression guard: collect_spec()/show_job() used to leave out
    filename_schema/filename_sep entirely -- a queued Import Loose job
    always converted with the page's default schema/separator regardless of
    what the user had actually typed when they queued it."""
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(tmp_path / "dest"))
    page._schema_edit.setText("plate:marker:well:site:tp")
    page._sep_edit.setText("-")

    spec = ctrl.collect_spec()
    assert spec["filename_schema"] == "plate:marker:well:site:tp"
    assert spec["filename_sep"] == "-"

    page.set_filename_schema("")
    page.set_filename_sep("")
    ctrl.show_job(spec)
    assert page.filename_schema() == "plate:marker:well:site:tp"
    assert page.filename_sep() == "-"
    ctrl.shutdown()


def test_in_place_success_reports_the_launched_source_not_a_since_changed_field(qapp, tmp_path):
    """Regression guard: the source PathField used to stay enabled for the
    whole run (unlike Subset's/Validate's locked fields), and
    _on_succeeded used to read self.page.source_path() (the live field)
    for in-place mode's "produced" root instead of what this run actually
    converted -- so a source change mid-run would make jobFinished's third
    argument (what "Open in Analyze" opens, and what a chained queue
    successor uses) name a folder this run never touched."""
    from datasets.import_loose.controller import ImportLooseController

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_loose(root_a, ["A01"])
    _write_loose(root_b, ["B02"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    ctrl._confirm_in_place = lambda: True  # bypass the modal QMessageBox
    page = ctrl.page
    page._src_field.set_path(str(root_a))
    page._on_source_changed()
    page._in_place_cb.setChecked(True)
    page._refresh_run_enabled()

    page.runRequested.emit()
    assert page._src_field.isEnabled() is False

    # The field changes after the run is launched but before it finishes.
    page._src_field.set_path(str(root_b))
    page._on_source_changed()
    _run_worker_to_completion(ctrl)
    assert page._src_field.isEnabled() is True

    assert ctrl._last_result[2] == str(root_a)
    ctrl.shutdown()


def test_request_stop_terminates_tracked_subprocess(tmp_path):
    """Regression guard: ImportLooseController.stop() used to call only
    QThread.requestInterruption() -- a no-op while run() is blocked inside
    convert_in_place's WellPlateZipper.py subprocess, since nothing on that
    path ever polls the interruption flag. request_stop() must also reach
    the tracked subprocess directly."""
    import subprocess
    import time

    from datasets.import_loose.worker import ImportLooseWorker

    worker = ImportLooseWorker(
        str(tmp_path), "", filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", in_place=True,
    )
    proc = subprocess.Popen(["sleep", "30"])
    worker._track_proc(proc)
    worker.request_stop()

    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None, "tracked subprocess should have been terminated"
    proc.wait(timeout=5)


def test_no_match_reports_failure_not_crash(qapp, tmp_path):
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    src.mkdir()
    (src / "readme.txt").write_text("hi")
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()

    seen = []
    ctrl._on_failed = lambda msg: (seen.append(msg), page.append_log(f"✗ {msg}"))
    page.runRequested.emit()
    _run_worker_to_completion(ctrl)
    assert seen and "No files matched" in seen[0]
    ctrl.shutdown()
