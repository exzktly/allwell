"""Image Browser container: a ``QTabWidget`` hosting the Segmentation tab.

The image-review surface is **Segmentation** (the review-image canvas). This
builds the sub-tab bar; the body (and its left sidebar) builds lazily on first
show via ``controller.build_image_browser_subtab``.
``core.tab_controller.current_centre_tab`` descends into this ``QTabWidget`` so
the existing leaf-name dispatch in ``on_tab_change`` keeps working.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from well_viewer.features.image_browser import controller as ctl

#: Sub-tab order. Leaf names must match the dispatch branches in
#: ``core.tab_controller.on_tab_change``. The active build profile filters this
#: (a profile may drop a sub-tab), so producing a subset is a manifest edit.
SUBTABS = ("Segmentation",)


def _active_subtabs() -> tuple:
    from well_viewer.core.profile import active_profile
    prof = active_profile()
    return tuple(s for s in SUBTABS if prof.includes_subtab("Image Browser", s))


def build_image_browser_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    sub_tabs = QTabWidget(parent)
    # Reuse the SampleDefinitionsSubTabs QSS (rounded rectangular tabs with a
    # min-width) so the bold selected-tab text isn't clipped — same pattern the
    # plotting sub-tab bars use.
    sub_tabs.setObjectName("SampleDefinitionsSubTabs")
    sub_tabs.tabBar().setExpanding(False)
    sub_tabs.tabBar().setElideMode(Qt.ElideNone)
    sub_tabs.tabBar().setUsesScrollButtons(True)
    layout.addWidget(sub_tabs)
    ctx.runtime.image_browser.subtabs = sub_tabs
    ctx.runtime.image_browser.built = {}

    # One empty frame per sub-tab; bodies build lazily (the build function sets
    # each sub-tab's own layout).
    for name in _active_subtabs():
        sub_tabs.addTab(QWidget(sub_tabs), name)

    sub_tabs.currentChanged.connect(
        lambda idx: ctl.on_image_browser_subtab_changed(ctx, idx))

    # Eagerly build the Segmentation review-image canvas so it's ready on open
    # (kept ready for the cell-review push flows).
    ctl.build_image_browser_subtab(ctx, sub_tabs.currentIndex())
