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
