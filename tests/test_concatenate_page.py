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
