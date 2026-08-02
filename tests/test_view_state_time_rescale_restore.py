"""The time-axis rescale factor must survive a dataset reopen.

``PlotState.time_scale`` changes what every time axis in a restored view reads,
so a saved view whose axis said 4, 8, 12 must not come back saying 1, 2, 3 with
nothing to explain the difference. Drives the real snapshot → restore round-trip,
the same shape as the FOV-toggle regression next door.
"""
from __future__ import annotations

import pytest

from well_viewer.persistence import view_state

pytestmark = pytest.mark.qt


def test_time_scale_round_trips(loaded_app):
    app = loaded_app

    app._app_state.plot.time_scale = 4.0
    snap = view_state.snapshot(app._ctx)
    assert snap["time_scale"] == 4.0

    # Simulate a fresh open, where the factor starts at its 1.0 off state.
    app._app_state.plot.time_scale = 1.0
    view_state.restore(app._ctx, snap)
    assert app._app_state.plot.time_scale == 4.0


def test_the_off_state_round_trips_too(loaded_app):
    app = loaded_app

    app._app_state.plot.time_scale = 1.0
    snap = view_state.snapshot(app._ctx)
    app._app_state.plot.time_scale = 4.0
    view_state.restore(app._ctx, snap)
    assert app._app_state.plot.time_scale == 1.0


def test_a_fractional_factor_survives_the_json_round_trip(loaded_app):
    import json
    app = loaded_app

    app._app_state.plot.time_scale = 0.25
    snap = json.loads(json.dumps(view_state.snapshot(app._ctx)))
    app._app_state.plot.time_scale = 1.0
    view_state.restore(app._ctx, snap)
    assert app._app_state.plot.time_scale == 0.25


def test_an_unusable_stored_factor_restores_the_off_state(loaded_app):
    """A hand-edited or truncated file could hold a zero or a negative, either
    of which would collapse or mirror every time axis. Restoring through
    ``coerce_factor`` turns that into "no rescale" rather than a broken plot."""
    app = loaded_app

    snap = view_state.snapshot(app._ctx)
    for bad in (0, -4, "nonsense", None):
        app._app_state.plot.time_scale = 4.0
        view_state.restore(app._ctx, {**snap, "time_scale": bad})
        assert app._app_state.plot.time_scale == 1.0


def test_a_snapshot_from_before_the_feature_leaves_the_factor_alone(loaded_app):
    """Older saved views carry no ``time_scale`` key at all."""
    app = loaded_app

    snap = view_state.snapshot(app._ctx)
    snap.pop("time_scale", None)
    app._app_state.plot.time_scale = 4.0
    view_state.restore(app._ctx, snap)
    assert app._app_state.plot.time_scale == 4.0
