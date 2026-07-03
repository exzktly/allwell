"""A8 burn-down pilot — review_csv widget handles moved into the WidgetRegistry.

The Review CSV tab builder used to stash its 5 Qt widgets on the app god-object
(`ctx.app._review_csv_table = ...`). They now go through `ctx.widgets.register`,
which also stamps `app._<name>` — so the legacy `app._review_*` readers in
review_image/controller.py keep resolving. This test proves the tab builds and
every legacy handle is present on the app after the build.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.review_csv.tab_view import build_review_csv_tab

_HANDLES = (
    "_review_well_lbl", "_review_fov_cb", "_review_tp_cb",
    "_review_csv_table", "_review_csv_msg_lbl",
)


def test_build_registers_handles_and_stamps_app(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)      # keep the parent (and its children) alive
    build_review_csv_tab(app._ctx, parent)

    for name in _HANDLES:
        # Registered in the WidgetRegistry…
        assert app._widget_registry.has(name), f"{name} not registered"
        # …and stamped onto the app so legacy `app._review_*` readers resolve.
        assert getattr(app, name, None) is not None, f"{name} not on app"
        # …and they're the same object.
        assert getattr(app, name) is app._widget_registry.get(name)


def test_refresh_reader_resolves_after_registry_build(viewer_app, qtbot):
    # review_image/controller.refresh_review_csv reads app._review_csv_table etc.
    # It must not raise now that those handles come from the registry.
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)      # keep the parent (and its children) alive
    build_review_csv_tab(app._ctx, parent)
    from well_viewer.features.image_browser.review_image.controller import (
        refresh_review_csv,
    )
    refresh_review_csv(app)  # no dataset loaded → the "select wells" branch; must not raise
    assert app._review_csv_msg_lbl.text()  # message label was updated by the reader
