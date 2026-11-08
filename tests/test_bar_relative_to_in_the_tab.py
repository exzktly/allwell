"""The real Bar tab builder puts *Then relative to* in its Fold change popover.

Full-app (``viewer_app``, slow lane): the fast tests drive the row through a
host widget, and this is the one that proves ``build_bar_plots_tab`` actually
builds it — CLAUDE.md §7.8 rule 3.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QComboBox, QWidget

from review.features.barplot.tab_view import build_bar_plots_tab


def test_the_bar_tab_builds_the_row_into_its_fold_change_popover(viewer_app, qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    build_bar_plots_tab(viewer_app._ctx, parent)
    row = viewer_app._ctx.widgets.get_or_none("_bar_rel_row")
    assert row is not None, "build_bar_plots_tab did not build the row"
    assert row.findChild(QComboBox, "barRelVariable") is not None
    # Inside the same panel as the shared control combo, i.e. the popover.
    ctrl = viewer_app._ctx.widgets.get_or_none("_fc_ctrl_combo_bar")
    assert ctrl is not None and ctrl.parentWidget() is row.parentWidget()
