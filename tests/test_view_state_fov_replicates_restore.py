"""Regression: the FOV-spread toggle must persist across a dataset reopen.

``use_fov_replicates`` (the "FOV" toggle on Line Graphs / Bar Plots) was not
captured in the view-state snapshot, so reopening a dataset always reverted it
to off. This drives the real snapshot → restore round-trip on a loaded app.
"""
from __future__ import annotations

import pytest

from well_viewer.persistence import view_state

pytestmark = pytest.mark.qt


def test_use_fov_replicates_round_trips(loaded_app):
    app = loaded_app

    app._app_state.plot.use_fov_replicates = True
    snap = view_state.snapshot(app._ctx)
    assert snap["use_fov_replicates"] is True

    # Simulate a fresh open where the toggle starts off, then restore.
    app._app_state.plot.use_fov_replicates = False
    view_state.restore(app._ctx, snap)
    assert app._app_state.plot.use_fov_replicates is True

    # And the off state round-trips too.
    snap_off = view_state.snapshot(app._ctx)
    assert snap_off["use_fov_replicates"] is True
    app._app_state.plot.use_fov_replicates = False
    snap2 = view_state.snapshot(app._ctx)
    view_state.restore(app._ctx, snap2)
    assert app._app_state.plot.use_fov_replicates is False
