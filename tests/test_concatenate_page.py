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
