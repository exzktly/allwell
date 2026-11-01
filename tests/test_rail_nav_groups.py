"""Review's section list shows the registry's groups as captions.

The registry has always declared Home / Analysis / Images / Data
(``FeatureDescriptor.group``), and the manual said the rail was "grouped into
Analysis, Images and Data", but the group chrome was retired with the old tab
bar and the rail drew eleven undifferentiated rows. ``RailNav.addCaption`` puts
a heading above each group; ``tab_controller.sync_section_nav_from_notebook``
places them from the descriptors, so no list of groups is written anywhere.

The grid half (phone layout's two columns) is in
``tests/test_rail_nav_group_grid.py``, a UI-lane file.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from review.features.registry import build_feature_registry
from widgets.rail_nav import RailNav


def _entries(nav):
    """The list's entries in order: a row's key, or ``"# <caption>"``."""
    lay = nav.layout()
    out = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        out.append(f"# {w._group_text}" if w.objectName() == "RailNavCaption" else w.key())
    return out


def _synced_nav(qtbot, features):
    from review.core.tab_controller import sync_section_nav_from_notebook
    host = QWidget()
    qtbot.addWidget(host)
    pages = [d.name for g in ("Home", "Analysis", "Images", "Data")
             for d in features.in_group(g)]
    win = SimpleNamespace(
        _section_nav=RailNav(host),
        _notebook=SimpleNamespace(pageNames=lambda: list(pages), count=lambda: len(pages),
                                  currentName=lambda: pages[0]),
        _section_nav_keys=[],
        _section_nav_building=False,
    )
    ctx = SimpleNamespace(window=win, features=features, host=host)
    sync_section_nav_from_notebook(ctx)
    return win._section_nav, ctx, pages


def test_each_group_after_home_opens_with_its_caption(qtbot):
    nav, _ctx, pages = _synced_nav(qtbot, build_feature_registry())
    assert nav.items() == pages, "captions must not become items"
    assert _entries(nav) == [
        "Home",
        # smFISH is an analysis step — it sets the spot-count threshold written
        # into the CSVs — not an image viewer.
        "# Analysis", "Plotting", "Statistics", "Multivariate", "Spatial", "Nanowells",
        "smFISH",
        "# Images", "Image Browser",
        "# Data", "Review CSV", "Saved Plots", "Batch Export",
    ]


def test_a_second_sync_adds_no_caption_twice(qtbot):
    from review.core.tab_controller import sync_section_nav_from_notebook
    nav, ctx, _pages = _synced_nav(qtbot, build_feature_registry())
    sync_section_nav_from_notebook(ctx)
    assert nav.captions() == ["Analysis", "Images", "Data"]


def test_a_caption_is_never_current_and_clicks_still_navigate(qtbot):
    nav, _ctx, _pages = _synced_nav(qtbot, build_feature_registry())
    got = []
    nav.currentChanged.connect(got.append)
    nav.setCurrentKey("Review CSV")
    assert nav.currentKey() == "Review CSV" and got == ["Review CSV"]


def test_a_caption_hides_while_its_whole_group_is_hidden(qtbot):
    nav = RailNav()
    qtbot.addWidget(nav)
    nav.addItem("Home")
    data = nav.addCaption("Data")
    for k in ("Review CSV", "Batch Export"):
        nav.addItem(k)
    nav._rows["Review CSV"].hide()
    assert not data.isHidden()
    nav._rows["Batch Export"].hide()
    assert data.isHidden(), "a heading stands over a group with nothing in it"
    nav._rows["Batch Export"].show()
    assert not data.isHidden()
