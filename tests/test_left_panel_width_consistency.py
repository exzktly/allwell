"""Regression guard: every mode's left panel pins to a fixed, named width, so a mode switch never resizes it to
an accident of its contents — the "jump wildly in width when switching" a user reported, when Datasets hardcoded
260, Review's rail was 340 and Analyze used only a *minimum* width (438) inside a resizable splitter.

Review's rail and Datasets' tool list are ``theme.Metrics.rail_w``. Analyze's form column is
``Metrics.form_column_w``: the design language gives Analyze no rail (tracker row D22), its form a column of
cards about 430 wide. The title bar's mode segment stays at ``rail_w`` in every mode, so the control a switch is
made with never moves under the pointer.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_analyze_form_column_is_fixed_at_its_own_width(qapp):
    from analyze.tab import AnalyzeTab
    from ui import theme
    tab = AnalyzeTab()
    sidebar = tab._left_sidebar
    assert sidebar.minimumWidth() == theme.Metrics.form_column_w
    assert sidebar.maximumWidth() == theme.Metrics.form_column_w


def test_datasets_left_sidebar_is_fixed_at_rail_w(qapp, tmp_path):
    from datasets.tab import DatasetsTab
    from ui import theme
    tab = DatasetsTab(queue_state_dir=tmp_path)
    sidebar = tab._left_sidebar
    assert sidebar.minimumWidth() == theme.Metrics.rail_w
    assert sidebar.maximumWidth() == theme.Metrics.rail_w


def test_each_mode_s_left_panel_is_its_named_width(qapp, tmp_path):
    """The actual regression: each mode's left panel is the width it names, not whatever its contents asked."""
    from analyze.tab import AnalyzeTab
    from datasets.tab import DatasetsTab
    from ui import theme
    analyze = AnalyzeTab()
    datasets = DatasetsTab(queue_state_dir=tmp_path)

    assert analyze._left_sidebar.width() == theme.Metrics.form_column_w
    assert datasets._left_sidebar.width() == theme.Metrics.rail_w
