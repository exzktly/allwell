"""Offscreen page tests for the Concatenate Datasets feature.

Same pattern as ``tests/test_validate_page.py`` / ``tests/test_subset_page.py``
/ ``tests/test_import_loose_page.py`` — build the page and drive its private
widgets directly, no ``viewer_app`` fixture (CLAUDE.md §2).
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


def test_run_button_restores_its_play_glyph_when_not_running(qapp):
    """Regression guard: every other Datasets tool's run button restores its
    "▶ " glyph once a run finishes (Validate's "▶  Check", Subset's
    "▶  Subset", Import Loose's / OME-TIFF's "▶  Convert", Merge Plates'
    "▶  Merge") -- Concatenate's was the one holdout, dropping back to a bare
    "Concatenate" with no glyph, which read as a different, unstarted-looking
    control next to its own "Concatenating…" running label."""
    from datasets.concatenate.view import ConcatenatePage

    page = ConcatenatePage()
    assert page._run_btn.text() == "▶  Concatenate"
    page.set_running(True)
    assert page._run_btn.text() == "Concatenating…"
    page.set_running(False)
    assert page._run_btn.text() == "▶  Concatenate"


def test_dashboard_hidden_until_both_datasets_are_valid_then_previews_real_wells(qapp, tmp_path):
    """Regression guard (UX audit P1-2): Concatenate's dashboard was the one
    dashboard in the pane with no hide/show logic at all -- visible from the
    moment the page loaded, painted with no wells (set_wells() only ever
    runs once a run's worker reports them), reading as "something is wrong"
    from page load. It must hide with no valid pair of datasets picked, and
    reveal seeded with the real union of both datasets' wells the moment
    both are valid -- driven through the fields' real PathField.changed
    signal (an interactive pick), not a direct set_path() call."""
    from datasets.concatenate.controller import ConcatenateController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])

    ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._ds1_field.set_path(str(ds1))
    page._ds1_field.changed.emit()
    assert page.dashboard.isHidden() is True  # dataset 2 not picked yet

    page._ds2_field.set_path(str(ds2))
    page._ds2_field.changed.emit()
    assert page.dashboard.isHidden() is False
    assert page.dashboard._wells == ["A01", "B02"]
    assert "Ready" in page.dashboard._headline.text()
    ctrl.shutdown()


def test_successful_run_shows_a_neutral_note_not_the_raw_already_has_content_warning(qapp, tmp_path):
    """Regression guard (UX audit P1-3): a completed Concatenate run writes
    its merged output into the destination, so revalidate_fields() (called
    from _on_finished to refresh the fields' inline hints) used to re-run
    the destination validator and land right back on "Already holds a
    finished dataset — choose an empty folder." -- the exact pre-run warning
    wording, now describing the run's own success as if something were
    wrong. It must show a neutral note instead."""
    from datasets.concatenate.controller import ConcatenateController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["A01"], timepoints=["48h"])

    ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._revalidate_cross()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert ctrl._last_result[0] is True  # the run actually succeeded
    assert page._dest_field._hint.objectName() == "Muted"
    assert "Already holds a finished dataset" not in page._dest_field._hint.text()
    assert "now holds the merged dataset" in page._dest_field._hint.text()
    # The run gate itself is unaffected by the note -- Concatenate is still
    # correctly disabled against the same now-occupied destination.
    assert not page._dest_field.is_valid()
    ctrl.shutdown()


def test_post_run_open_button_is_promoted_and_run_button_demoted(qapp, tmp_path):
    """Regression guard (UX audit P2-2): the "Open merged dataset in Review"
    button appeared on success styled the same secondary way it always was,
    right next to a still-primary-styled Concatenate button that has nothing
    useful left to do until the inputs change -- the visual emphasis stayed
    on the wrong control. Success must promote Open to primary and demote
    Concatenate to secondary; picking a new dataset afterward (the point a
    fresh run becomes the relevant action again) must flip it back."""
    from datasets.concatenate.controller import ConcatenateController

    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["A01"], timepoints=["48h"])

    ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    assert page._run_btn.property("variant") == "primary"
    assert page._open_btn.property("variant") == "secondary"

    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    page._revalidate_cross()
    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert ctrl._last_result[0] is True
    assert page._open_btn.property("variant") == "primary"
    assert page._run_btn.property("variant") == "secondary"

    # Picking dataset 1 again (any interactive input change) hands the
    # primary emphasis back to the run button.
    page._ds1_field.set_path(str(ds1))
    page._ds1_field.changed.emit()
    assert page._run_btn.property("variant") == "primary"
    ctrl.shutdown()
