"""A8 state migration — scatter mode/color_by_tp/gates_hidden → ScatterState.

The active sub-tab, the per-cell "colour by timepoint" toggle, and the
"hide gates" toggle used to live on the god-object as ``app._scatter_mode`` /
``_scatter_color_by_tp`` / ``_scatter_gates_hidden``. They now live on the typed
``ScatterState`` slice (``app._app_state.scatter.*``). This proves the slice
defaults, that the god-object attributes are gone, and that the active-mode
persists through the view-state round-trip.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.scatter.tab_view import scatter_active_mode
from well_viewer.persistence import view_state as vs


def test_slice_defaults(viewer_app):
    sc = viewer_app._app_state.scatter
    assert sc.mode == "cells"
    assert sc.color_by_tp is False
    assert sc.gates_hidden is False
    # The god-object attributes are gone.
    assert not hasattr(viewer_app, "_scatter_mode")
    assert not hasattr(viewer_app, "_scatter_color_by_tp")
    assert not hasattr(viewer_app, "_scatter_gates_hidden")


def test_active_mode_reads_the_slice(viewer_app):
    app = viewer_app
    assert scatter_active_mode(app._ctx) == "cells"
    app._app_state.scatter.mode = "aggregate"
    assert scatter_active_mode(app._ctx) == "aggregate"


def test_mode_persists_through_snapshot(viewer_app):
    app = viewer_app
    app._app_state.scatter.mode = "aggregate"
    snap = vs.snapshot(app)
    assert snap["tabs"]["Scatter Plot"]["mode"] == "aggregate"
