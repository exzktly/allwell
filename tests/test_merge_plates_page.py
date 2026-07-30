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


def test_dashboard_hidden_until_run_starts(qapp, tmp_path):
    """Regression guard: the status strip below the button row used to be
    visible from page load. Uses isHidden() (a local, explicit hide/show
    flag) rather than isVisible(), since the page is never actually shown
    in this test — isVisible() would report False for the whole tree
    regardless of these setVisible() calls."""
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()
    assert page.dashboard.isHidden() is True  # still nothing has run

    page.runRequested.emit()
    assert page.dashboard.isHidden() is False  # shown the moment Merge starts
    _run_worker_to_completion(ctrl)
    assert page.dashboard.isHidden() is False  # stays up to show the result
    ctrl.shutdown()


def test_dashboard_shown_even_when_remap_fails_before_the_worker_starts(qapp, tmp_path):
    """The remap-capacity check (a shift pushing a well past column 12) runs
    inside _launch() itself, before any SubsetWorker starts — unlike an
    invalid origin token, _remap_message()'s own pre-check doesn't catch
    this, so the run gate lets it through. With the raw log hidden by
    default the dashboard is the only feedback the user gets, so it must be
    shown here too, not just on the normal worker-launch path."""
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    # A01+A03 span columns 1 and 3 (width 3). block_shift_well_map anchors
    # the block's own minimum corner (A01) at the target origin, so a
    # single-well dataset could never overflow this way — it always lands
    # exactly on the (already-validated) origin. Two wells are needed so the
    # *other* one can be pushed past column 12 while the anchor itself stays
    # in range.
    build_dataset(ds2, wells=["A01", "A03"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._remap_cb.setChecked(True)
    page._remap_origin_edit.setText("A11")  # shifts A03 to column 13 — out of range
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()  # _remap_message() doesn't catch this

    page.runRequested.emit()
    assert ctrl._worker is None  # failed before a worker was ever created
    assert page.dashboard.isHidden() is False
    assert "Failed" in page.dashboard._headline.text()
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
