"""A8 state migration — heat-map scale/repset/log flags moved to HeatmapState.

The colour-scale mode and the two display toggles used to live on the
god-object as ``app._heatmap_scale_mode`` / ``_heatmap_repset_avg`` /
``_heatmap_log_scale``. They now live on the typed ``HeatmapState`` slice
(``app._app_state.heatmap.*``). This proves the slice defaults, that the
god-object attributes are gone, and that the per-layout persistence
round-trips through the slice.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.heatmap.tab_view import build_heatmap_tab
from well_viewer.persistence import heatmap_layouts as hl


def test_slice_defaults(viewer_app):
    hm = viewer_app._app_state.heatmap
    assert hm.scale_mode == "Auto"
    assert hm.repset_avg is False
    assert hm.log_scale is False
    # The god-object attributes are gone.
    assert not hasattr(viewer_app, "_heatmap_scale_mode")
    assert not hasattr(viewer_app, "_heatmap_repset_avg")
    assert not hasattr(viewer_app, "_heatmap_log_scale")


def test_settings_collect_reads_the_slice(viewer_app):
    app = viewer_app
    app._app_state.heatmap.scale_mode = "Fixed"
    app._app_state.heatmap.repset_avg = True
    app._app_state.heatmap.log_scale = True
    settings = hl._collect_settings(app)
    assert settings["scale_mode"] == "Fixed"
    assert settings["repset_avg"] is True
    assert settings["log_scale"] is True


def test_persistence_round_trip_through_slice(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_heatmap_tab(app._ctx, parent)

    app._app_state.heatmap.scale_mode = "Fixed"
    app._app_state.heatmap.repset_avg = True
    app._app_state.heatmap.log_scale = True
    settings = hl._collect_settings(app)

    # Flip the live slice back to defaults, then restore through the real apply
    # path (which reads app._heatmap_persisted_settings) — the slice must follow.
    app._app_state.heatmap.scale_mode = "Auto"
    app._app_state.heatmap.repset_avg = False
    app._app_state.heatmap.log_scale = False
    app._heatmap_persisted_settings = settings
    hl.apply_persisted_settings(app)
    assert app._app_state.heatmap.scale_mode == "Fixed"
    assert app._app_state.heatmap.repset_avg is True
    assert app._app_state.heatmap.log_scale is True
