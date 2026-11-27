"""The Scatter cell viewer is the inspector's Detail tab, on the built application.

Clicking a point on the Scatter plot opens a single-cell image viewer. It was
a modal dialog, then a slide-out sharing the plot's right dock with the
Properties panel — so only one of the two could be seen. It is the inspector's
Detail tab now, beside Style: both are one press apart and neither hides the
other for good.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def _build_scatter_tab(app) -> None:
    assert app._notebook.setCurrentByName("Plotting")
    assert app._plotting_notebook.setCurrentByName("Scatter Plot")


def test_cell_viewer_is_a_widget_not_a_dialog():
    # It must be dockable (a plain QWidget), never a modal dialog window.
    from PySide6.QtWidgets import QDialog, QWidget
    from review.features.scatter.cell_viewer_view import ScatterCellViewer

    assert issubclass(ScatterCellViewer, QWidget)
    assert not issubclass(ScatterCellViewer, QDialog)


def test_cell_viewer_is_the_inspector_s_detail_tab(loaded_app):
    from review.core import inspector_service as svc
    app = loaded_app
    _build_scatter_tab(app)

    app._open_scatter_cell_viewer("A01", "", "1", 0)
    viewer = app._ctx.runtime.scatter.cell_viewer
    assert viewer is not None
    insp = svc.inspector(app._ctx)
    assert svc.is_open(app._ctx)
    assert insp.tabWidget("detail") is viewer and insp.currentTab() == "detail"
    assert insp.isAncestorOf(viewer) and not viewer.isHidden()


def test_cell_viewer_and_style_are_two_tabs_of_one_inspector(loaded_app):
    from review.core import inspector_service as svc
    app = loaded_app
    _build_scatter_tab(app)
    insp = svc.inspector(app._ctx)

    app._open_scatter_cell_viewer("A01", "", "1", 0)
    viewer = app._ctx.runtime.scatter.cell_viewer
    assert insp.currentTab() == "detail"

    # Opening Style shows the Style tab; the viewer keeps its tab, one press away.
    app._ctx.figures.open_style_panel("scatter_cells")
    sidebar = app._export_style_sidebars.get(id(app._scatter_fig))
    assert sidebar is not None and insp.tabWidget("style") is sidebar
    assert insp.currentTab() == "style"
    assert insp.tabWidget("detail") is viewer and insp.isTabEnabled("detail")

    # Picking another cell brings Detail back to the front, in the same viewer.
    app._open_scatter_cell_viewer("A02", "", "2", 0)
    assert insp.currentTab() == "detail" and insp.tabWidget("detail") is viewer
    assert insp.tabWidget("style") is sidebar
