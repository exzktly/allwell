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
