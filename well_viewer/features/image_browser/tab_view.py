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

#: Sub-tab order (Image Table first — the primary image-review surface). Leaf
#: names must match the dispatch branches in ``core.tab_controller.on_tab_change``.
#: The active build profile filters this, so producing a subset (e.g. the thin
#: build's Segmentation-only Image Browser) is a manifest edit, not a code edit.
SUBTABS = ("Image Table", "Segmentation", "Cell Browser", "Playground")


def _active_subtabs(ctx) -> tuple:
    """The sub-tabs this build ships, in the order above.

    Membership comes from the registry -- ``sub_leaves`` is already filtered by
    the active profile, and honours ``removable=False``. It must not come from
    ``Profile.includes_subtab``, which is a bare string check against the
    ``[subtabs]`` allowlist: that ignores the flag, so a manifest narrowing this
    parent used to keep the ``Segmentation`` *descriptor* while the notebook
    never created its *tab*. Home was moved off the same predicate for the same
    reason (issue #14).

    The order stays here rather than following declaration order, because it is a
    presentation decision this file owns: Image Table leads as the primary
    image-review surface and is the one built eagerly below.
    ``test_image_browser_subtabs_match_the_registry`` keeps this tuple and the
    registry from disagreeing about membership.
    """
    kept = set(ctx.features.sub_leaves("Image Browser"))
    return tuple(s for s in SUBTABS if s in kept)


def build_image_browser_tab(ctx, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    sub_tabs = QTabWidget(parent)
    # Every nested sub-tab bar in the pane — this one, Plotting's and Home's —
    # is named in one theme.qss rule, so the three cannot drift
    # apart. The name is this bar's own; it used to borrow another page's,
    # which made the rule read as if only that tab had a bar.
    sub_tabs.setObjectName("ImageBrowserSubTabs")
    sub_tabs.tabBar().setExpanding(False)
    sub_tabs.tabBar().setElideMode(Qt.ElideNone)
    sub_tabs.tabBar().setUsesScrollButtons(True)
    layout.addWidget(sub_tabs)
    ctx.runtime.image_browser.subtabs = sub_tabs
    ctx.runtime.image_browser.built = {}

    # One empty frame per sub-tab; bodies build lazily (the build function sets
    # each sub-tab's own layout, so the frames stay layout-less here — Playground
    # in particular needs a QHBoxLayout, not the QVBoxLayout the others use).
    for name in _active_subtabs(ctx):
        sub_tabs.addTab(QWidget(sub_tabs), name)

    sub_tabs.currentChanged.connect(
        lambda idx: ctl.on_image_browser_subtab_changed(ctx, idx))

    # The visible bar lives in the shell's mode band, beside the mode selector,
    # not above this tab's body. ``mirror_tab_widget`` hides this widget's own
    # bar and drives it from one up there, so every reader of the QTabWidget's
    # API (the controller, view-state, the tests) is untouched.
    from well_viewer.core.app_shell_view import mirror_tab_widget
    mirror_tab_widget(ctx.window, "Image Browser", sub_tabs)

    # Eagerly build the initial sub-tab so it's ready on open, plus the
    # Segmentation review-image canvas (kept ready for the CSV / cell-review push
    # flows) when present. Cell Browser / Playground stay lazy — built on first
    # sub-tab click.
    ctl.build_image_browser_subtab(ctx, sub_tabs.currentIndex())
    for i in range(sub_tabs.count()):
        if sub_tabs.tabText(i) == "Segmentation":
            ctl.build_image_browser_subtab(ctx, i)
            break
