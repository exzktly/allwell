"""Regression guard: Review's rail, Datasets' sidebar, and Analyze's form
panel must all pin to the same fixed width (review.core.app_shell_view.
RAIL_W) so switching between the three top-level modes never visibly resizes
the window. Datasets previously hardcoded 260 (Review's rail is 340, later
widened) and Analyze used only a *minimum* width (438) inside a resizable
splitter — three different, independently-drifting numbers, exactly the
"jump wildly in width when switching" a user reported.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_analyze_left_sidebar_is_fixed_at_rail_w(qapp):
    from analyze.tab import AnalyzeTab
    from review.core.app_shell_view import RAIL_W

    tab = AnalyzeTab()
    sidebar = tab._left_sidebar
    assert sidebar.minimumWidth() == RAIL_W
    assert sidebar.maximumWidth() == RAIL_W


def test_datasets_left_sidebar_is_fixed_at_rail_w(qapp, tmp_path):
    from datasets.tab import DatasetsTab
    from review.core.app_shell_view import RAIL_W

    tab = DatasetsTab(queue_state_dir=tmp_path)
    sidebar = tab._left_sidebar
    assert sidebar.minimumWidth() == RAIL_W
    assert sidebar.maximumWidth() == RAIL_W


def test_review_and_datasets_and_analyze_agree_on_one_width(qapp, tmp_path):
    """The actual regression: all three modes' left panel must be the exact
    same width, not merely each internally self-consistent."""
    from analyze.tab import AnalyzeTab
    from datasets.tab import DatasetsTab
    from review.core.app_shell_view import RAIL_W

    analyze = AnalyzeTab()
    datasets = DatasetsTab(queue_state_dir=tmp_path)

    assert analyze._left_sidebar.width() == RAIL_W == datasets._left_sidebar.width()
