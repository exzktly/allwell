"""Regression guards for UX audit P2-8: section-header/dashboard-title labels
that collided across two Datasets tools, reading as if the wrong tool's
status section were showing.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _labels(widget) -> set[str]:
    return {lbl.text() for lbl in widget.findChildren(QLabel)}


def test_concatenate_and_merge_plates_no_longer_share_merge_output(qapp):
    """"Merge Output" used to be both Concatenate's and Merge Plates' status
    section header — indistinguishable from a screenshot of either page."""
    from datasets.concatenate.view import ConcatenatePage
    from datasets.merge_plates.view import MergePlatesPage

    concat_labels = _labels(ConcatenatePage())
    merge_labels = _labels(MergePlatesPage())

    assert "Concatenate Output" in concat_labels
    assert "Merge Output" not in concat_labels
    assert "Merge Output" in merge_labels


def test_concatenate_and_merge_plates_dashboards_no_longer_share_merge_progress(qapp):
    """Same collision, one level down: both dashboards' own title read
    "Merge Progress"."""
    from datasets.concatenate.dashboard import ConcatDashboard
    from datasets.merge_plates.dashboard import MergePlatesDashboard

    assert ConcatDashboard()._title_label.text() == "Concatenate Progress"
    assert MergePlatesDashboard()._title_label.text() == "Merge Progress"


def test_ome_import_and_import_loose_no_longer_share_conversion_output(qapp):
    """"Conversion Output" used to be both OME-TIFF's and Import Loose's
    status section header."""
    from datasets.import_loose.view import ImportLoosePage
    from datasets.ome_import.view import OmeImportPage

    ome_labels = _labels(OmeImportPage())
    loose_labels = _labels(ImportLoosePage())

    assert "Convert Output" in ome_labels
    assert "Conversion Output" not in ome_labels
    assert "Conversion Output" in loose_labels


def test_concatenate_heading_matches_the_rest_of_the_codebase_title_case(qapp):
    """Every other reference to this feature (docstrings, the class body
    comment, the rail-nav entry) already writes "Concatenate Datasets" —
    only the on-screen heading label and the sidebar entry name still said
    "Concatenate datasets" (sentence case), the one outlier among six
    title-cased page headings ("Validate & Repair Dataset", "Subset / Split
    Dataset", "Import Loose Folder", "Merge Plates", "Convert OME-TIFF",
    "Job Queue")."""
    from datasets.concatenate.view import ConcatenatePage

    assert "Concatenate Datasets" in _labels(ConcatenatePage())


def test_sidebar_entry_matches_the_page_heading_case(qapp):
    from datasets_tab import _FEATURES

    names = [feat.name for feat in _FEATURES]
    assert "Concatenate Datasets" in names
    assert "Concatenate datasets" not in names


def test_merge_plates_blurb_says_datasets_not_all_caps_plates(qapp):
    """Regression guard (UX audit P2-8/P2-10): the blurb called the two
    source folders "plates" ("Join two DIFFERENT plates…") while every
    field/legend on the same page calls them "dataset 1"/"dataset 2" — one
    word per unit is the rule everywhere else in the pane ("plate" reserved
    for the 96-well grid). It also used ALL-CAPS ("DIFFERENT", "SAME") to do
    a job typography should do."""
    from datasets.merge_plates.view import MergePlatesPage

    labels = _labels(MergePlatesPage())
    blurb = next(t for t in labels if t.startswith("Join two"))
    assert "DIFFERENT" not in blurb
    assert "SAME" not in blurb
    assert "different datasets" in blurb
    assert "same dataset" in blurb


def test_validate_blurb_moves_the_pipeline_info_detail_into_a_tooltip(qapp):
    """Regression guard (UX audit P2-10): the blurb led with an
    implementation detail (pipeline_info.json) — moved to the label's own
    tooltip, the on-page text stays a plain-language summary."""
    from datasets.validate.view import ValidatePage
    from PySide6.QtWidgets import QLabel

    page = ValidatePage()
    blurb = next(lbl for lbl in page.findChildren(QLabel) if "Check what" in lbl.text())
    assert "pipeline_info.json" not in blurb.text()
    assert "pipeline_info.json" in blurb.toolTip()
