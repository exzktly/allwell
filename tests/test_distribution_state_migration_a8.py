"""A8 state migration — distribution bins/log_x/log_y moved to DistributionState.

The histogram bin count and the two log-axis toggles used to live on the
god-object as ``app._distribution_bins`` / ``_distribution_log_x`` /
``_distribution_log_y``. They now live on the typed ``DistributionState`` slice
(``app._app_state.distribution.*``). This proves the slice defaults, that the
god-object attributes are gone, and that the view-state persistence
round-trips through the slice.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.persistence import view_state as vs


def test_slice_defaults(viewer_app):
    dist = viewer_app._app_state.distribution
    assert dist.bins == 40
    assert dist.log_x is False
    assert dist.log_y is False
    # The god-object attributes are gone.
    assert not hasattr(viewer_app, "_distribution_bins")
    assert not hasattr(viewer_app, "_distribution_log_x")
    assert not hasattr(viewer_app, "_distribution_log_y")


def test_persistence_round_trip_through_slice(viewer_app):
    app = viewer_app
    app._app_state.distribution.bins = 128
    app._app_state.distribution.log_x = True
    app._app_state.distribution.log_y = False
    snap = vs.snapshot(app._ctx)
    dist_snap = snap["tabs"]["Distribution"]
    assert dist_snap["bins"] == 128
    assert dist_snap["log_x"] is True
    assert dist_snap["log_y"] is False

    # Flip the live slice, then apply the saved state — the slice must follow.
    app._app_state.distribution.bins = 40
    app._app_state.distribution.log_x = False
    vs._apply_distribution(app._ctx, dist_snap)
    assert app._app_state.distribution.bins == 128
    assert app._app_state.distribution.log_x is True
    assert app._app_state.distribution.log_y is False
