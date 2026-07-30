"""Offscreen controller/page tests for the Merge Plates feature.

Same pattern as ``tests/test_validate_page.py`` / ``tests/test_subset_page.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.fixtures.dataset_builder import build_dataset  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run_worker_to_completion(ctrl) -> None:
    worker = ctrl._worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(10):
        QApplication.processEvents()


def test_run_gate_requires_three_distinct_paths(qapp, tmp_path):
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert not page._run_btn.isEnabled()
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._refresh_run_enabled()
    assert not page._run_btn.isEnabled()  # no destination yet

    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()
    ctrl.shutdown()


def test_identity_merge_through_the_page(qapp, tmp_path):
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert (dest / "out" / "measurements_A01.csv").exists()
    assert (dest / "out" / "measurements_B02.csv").exists()
    assert not page._open_btn.isHidden()
    ctrl.shutdown()


def test_collision_without_remap_fails_gracefully(qapp, tmp_path):
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])  # collides

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert not (dest / "out" / "pipeline_info.json").exists()
    ctrl.shutdown()


def test_remap_field_validates_well_token(qapp, tmp_path):
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(tmp_path / "dest"))
    page._remap_cb.setChecked(True)
    page._remap_origin_edit.setText("Z99")
    page._refresh_run_enabled()
    assert not page._run_btn.isEnabled()

    page._remap_origin_edit.setText("E01")
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()
    ctrl.shutdown()


def test_remap_run_places_dataset_two_at_origin(qapp, tmp_path):
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._remap_cb.setChecked(True)
    page._remap_origin_edit.setText("E01")
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert (dest / "out" / "measurements_E01.csv").exists()
    assert (dest / "out" / "measurements_A01.csv").exists()
    ctrl.shutdown()
