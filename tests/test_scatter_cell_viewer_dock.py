"""Scatter cell viewer as a shared-dock slide-out (not a modal pop-up).

Clicking a point on the Scatter plot opens a single-cell image viewer. It used
to be a disruptive modal ``QDialog`` window; it now slides out in the Scatter
tab's right dock — the *same* dock the Export-Style / Properties sidebar uses —
so only one of them is visible at a time.
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
    from well_viewer.features.scatter.callbacks import ScatterCellViewer

    assert issubclass(ScatterCellViewer, QWidget)
    assert not issubclass(ScatterCellViewer, QDialog)


def test_cell_viewer_slides_into_scatter_dock(loaded_app):
    app = loaded_app
    _build_scatter_tab(app)
    dock = app._scatter_export_dock
    assert dock is not None

    app._open_scatter_cell_viewer("A01", "", "1", 0)
    viewer = app._scatter_cell_viewer
    assert viewer is not None
    # The viewer lives inside the shared dock and is the visible panel.
    assert viewer.parentWidget() is dock
    assert not viewer.isHidden()
    assert not dock.isHidden()


def test_cell_viewer_and_properties_are_mutually_exclusive(loaded_app):
    app = loaded_app
    _build_scatter_tab(app)
    dock = app._scatter_export_dock

    # Open the cell viewer first.
    app._open_scatter_cell_viewer("A01", "", "1", 0)
    viewer = app._scatter_cell_viewer
    assert not viewer.isHidden()

    # Opening the Properties / Export-Style sidebar hides the cell viewer
    # (one panel at a time in the shared dock).
    app._open_export_style_panel("scatter_cells")
    assert viewer.isHidden()
    sidebar = app._export_style_sidebars.get(id(app._scatter_fig))
    assert sidebar is not None and not sidebar.isHidden()

    # Re-opening the cell viewer hides the sidebar again.
    app._open_scatter_cell_viewer("A02", "", "2", 0)
    assert not viewer.isHidden()
    assert sidebar.isHidden()
