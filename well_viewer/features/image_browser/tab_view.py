"""Image Browser container: a ``QTabWidget`` hosting the four image-review tabs.

The disparate image-review surfaces — **Segmentation** (the review-image
canvas), **Image Table**, **Cell Browser**, and **Playground** — were unified
under a single rail entry. This builds the sub-tab bar; each sub-tab body (and
its left sidebar, where it has one) builds lazily on first show via
``controller.build_image_browser_subtab``, mirroring the old Segmentation
sub-tab pattern. ``core.tab_controller.current_centre_tab`` descends into this
``QTabWidget`` so the existing leaf-name dispatch in ``on_tab_change`` keeps
working.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from well_viewer.features.image_browser import controller as ctl

#: Sub-tab order. Leaf names must match the dispatch branches in
#: ``core.tab_controller.on_tab_change``.
SUBTABS = ("Segmentation", "Image Table", "Cell Browser", "Playground")


def build_image_browser_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    sub_tabs = QTabWidget(parent)
    # Reuse the SampleDefinitionsSubTabs QSS (rounded rectangular tabs with a
    # min-width) so the bold selected-tab text isn't clipped — same pattern the
    # plotting / old-Segmentation sub-tab bars use.
    sub_tabs.setObjectName("SampleDefinitionsSubTabs")
    sub_tabs.tabBar().setExpanding(False)
    sub_tabs.tabBar().setElideMode(Qt.ElideNone)
    sub_tabs.tabBar().setUsesScrollButtons(True)
    layout.addWidget(sub_tabs)
    ctx.app._image_browser_subtabs = sub_tabs
    ctx.runtime.image_browser.built = {}

    # One empty frame per sub-tab; bodies build lazily (the build function sets
    # each sub-tab's own layout, so the frames stay layout-less here — Playground
    # in particular needs a QHBoxLayout, not the QVBoxLayout the others use).
    for name in SUBTABS:
        sub_tabs.addTab(QWidget(sub_tabs), name)

    sub_tabs.currentChanged.connect(
        lambda idx: ctl.on_image_browser_subtab_changed(ctx.app, idx))

    # Eagerly build Segmentation (the initial, current sub-tab) so the canvas
    # is ready on first open. Image Table / Cell Browser / Playground stay lazy:
    # built on first sub-tab click.
    ctl.build_image_browser_subtab(ctx.app, sub_tabs.currentIndex())
