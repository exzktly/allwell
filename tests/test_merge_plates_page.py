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


def test_successful_run_shows_a_neutral_note_not_the_raw_already_has_content_warning(qapp, tmp_path):
    """Regression guard (UX audit P1-3): a completed Merge Plates run writes
    its merged output into the destination, so revalidate_fields() (called
    from _on_finished to refresh the fields' inline hints) used to re-run
    the destination validator and land right back on "Already holds a
    finished dataset — choose an empty folder." -- the exact pre-run warning
    wording, now describing the run's own success as if something were
    wrong. It must show a neutral note instead."""
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

    assert ctrl._last_result[0] is True
    assert page._dest_field._hint.objectName() == "Muted"
    assert "Already holds a finished dataset" not in page._dest_field._hint.text()
    assert "now holds the merged plate" in page._dest_field._hint.text()
    assert not page._dest_field.is_valid()
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


def test_interactive_input_reveals_a_preview_seeded_with_real_wells(qapp, tmp_path):
    """Regression guard (UX audit P1-2): once both datasets are valid, the
    status strip must reveal itself seeded with the wells the merge would
    actually produce -- including the remap, when one is set -- rather than
    staying hidden until Run is pressed. Drives the fields through their
    real PathField.changed signal (an interactive pick), not a direct
    set_path()+_refresh_run_enabled() call, since that's the path this
    preview is wired to."""
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
    page._ds1_field.changed.emit()
    assert page.dashboard.isHidden() is True  # dataset 2 not picked yet

    page._ds2_field.set_path(str(ds2))
    page._ds2_field.changed.emit()
    assert page.dashboard.isHidden() is False
    assert page.dashboard._wells == ["A01", "B02"]
    assert page.dashboard._well_origin == {"A01": "ds1", "B02": "ds2"}
    assert "Ready" in page.dashboard._headline.text()

    # Enabling the remap shifts the preview's dataset-2 well, not just the
    # eventual run's.
    page._remap_cb.setChecked(True)
    page._remap_origin_edit.setText("C03")
    page._remap_origin_edit.textChanged.emit("C03")
    assert page.dashboard._wells == ["A01", "C03"]
    assert page.dashboard._well_origin == {"A01": "ds1", "C03": "ds2"}
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


def test_fields_locked_during_a_run(qapp, tmp_path):
    """Regression guard: the dataset1/dataset2/destination PathFields and the
    remap controls stayed enabled for the whole run, unlike Subset's own
    fields -- MergePlatesController._on_wells_planned re-resolves
    dataset1_path()/dataset2_path() live to colour the dashboard by origin,
    so a mid-run change could misattribute which wells came from which
    dataset."""
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
    assert page._ds1_field.isEnabled() is False
    assert page._ds2_field.isEnabled() is False
    assert page._dest_field.isEnabled() is False
    assert page._remap_cb.isEnabled() is False
    _run_worker_to_completion(ctrl)
    assert page._ds1_field.isEnabled() is True
    assert page._ds2_field.isEnabled() is True
    assert page._dest_field.isEnabled() is True
    assert page._remap_cb.isEnabled() is True
    ctrl.shutdown()


def test_origin_colours_use_the_launched_paths_not_a_since_changed_field(qapp, tmp_path):
    """Regression guard: _on_wells_planned used to re-read
    dataset2_path() live instead of the path this run actually launched
    with -- if dataset 2's field changed after the run started but before
    wellsPlanned fired, the dashboard's per-well origin colouring would be
    computed against a dataset this run never touched."""
    from datasets.merge_plates.controller import MergePlatesController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    ds2_other = tmp_path / "ds2_other"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    build_dataset(ds2_other, wells=["C03"], timepoints=["0h"])

    ctrl = MergePlatesController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    # The field changes after the run is launched but before it finishes —
    # possible even though the field is disabled, since setPath() is a
    # direct call, not a click through the (locked) widget.
    page._ds2_field.set_path(str(ds2_other))
    _run_worker_to_completion(ctrl)

    assert page.dashboard._well_origin.get("B02") == "ds2"
    assert "C03" not in page.dashboard._well_origin
    ctrl.shutdown()
