"""Loading a dataset with Image Table active must populate it — no tab-switch / drag.

Regression for the reported bug: with Image Table the active tab at load time, the
well-plate stayed empty until the user switched tabs and back, and the builder shelf's
channel chips stayed empty until the user dragged a well chip onto the Rows zone.

Cause: sidebar/per-tab population is driven by ``on_tab_change`` -> the active leaf's
``on_activate``, but a same-tab (re)load emits no ``currentChanged`` so activation
never re-ran; and nothing on the load/activate path refreshed the builder shelf. The
fix re-activates the current tab at the end of ``load_directory`` and has
``_activate_image_table`` call ``builder_refresh``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QFrame

from well_viewer.core.tab_controller import current_centre_tab


@pytest.mark.slow
def test_image_table_populates_on_load_without_tab_switch(qtbot, mini_dataset):
    from well_viewer.features.image_browser.controller import build_image_browser_subtab
    from well_viewer.runtime_app import WellViewerApp

    app = WellViewerApp()
    qtbot.addWidget(app)

    # Build Image Browser + the Image Table sub-tab and make it the ACTIVE tab
    # BEFORE loading — the exact state in which the bug reproduced.
    build_pending = app._ctx.runtime.centre.build_pending
    if build_pending is not None:
        build_pending("Image Browser")
    app._notebook.setCurrentByName("Image Browser")
    sub = app._ctx.runtime.image_browser.subtabs
    assert sub is not None
    idx = next(i for i in range(sub.count()) if sub.tabText(i) == "Image Table")
    build_image_browser_subtab(app._ctx, idx)
    sub.setCurrentIndex(idx)
    qtbot.wait(0)
    assert current_centre_tab(app._ctx) == "Image Table"

    # Load with Image Table already active — no tab switch, no drag afterwards.
    app._load_path(mini_dataset)
    qtbot.wait(0)

    # Symptom 2: the builder shelf's channel chips populate from the dataset's
    # channels (gfp / mcherry) — one ITBChip per channel, not the empty placeholder.
    tray = app._ctx.runtime.image_table.channels_tray_body
    chips = tray.findChildren(QFrame, "ITBChip")
    assert len(chips) >= 1, "builder channel chips did not populate on load"

    # Symptom 1: the Image Table's own picker plate is populated (enabled wells
    # set from the loaded dataset) without a tab switch — this is what the
    # re-activation re-runs via image_table_refresh_picker.
    picker = app._ctx.runtime.image_table.sidebar_plate
    assert picker is not None
    assert picker._grid._enabled, "Image Table picker wells not populated on load"
