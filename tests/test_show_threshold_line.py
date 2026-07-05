"""The cross-plot threshold marker is off by default and round-trips through
view_state (so the Properties-sidebar toggle persists)."""

from __future__ import annotations

import pytest

from well_viewer.core.plot_state import PlotState


def test_threshold_line_off_by_default():
    # Pure (Qt-free): the new option ships hidden — opt in via the sidebar.
    assert PlotState().show_threshold_line is False


@pytest.mark.qt
def test_show_threshold_line_round_trips_through_view_state(loaded_app):
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.show_threshold_line = True
    snap = vs.snapshot(app._ctx)
    assert snap.get("show_threshold_line") is True

    app._app_state.plot.show_threshold_line = False
    vs.restore(app._ctx, snap)
    assert app._app_state.plot.show_threshold_line is True
