"""A8 state migration (pilot) — bar swarm/violin flags moved to BarPlotState.

The per-cell overlay flags used to live on the god-object as ``app._bar_swarm`` /
``app._bar_violin``. They now live on the typed ``BarPlotState`` slice
(``app._app_state.bar.swarm`` / ``.violin``). This proves the controller toggles,
the mutual-exclusion, and the persistence round-trip all operate on the slice.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.barplot.controller import toggle_swarm, toggle_violin
from well_viewer.persistence import view_state as vs


def test_slice_defaults_false(viewer_app):
    bar = viewer_app._app_state.bar
    assert bar.swarm is False and bar.violin is False
    # The god-object attributes are gone.
    assert not hasattr(viewer_app, "_bar_swarm")
    assert not hasattr(viewer_app, "_bar_violin")


def test_toggles_are_mutually_exclusive_on_the_slice(viewer_app):
    app = viewer_app
    bar = app._app_state.bar
    toggle_swarm(app._ctx)
    assert bar.swarm is True and bar.violin is False
    toggle_violin(app._ctx)                 # turning violin on clears swarm
    assert bar.violin is True and bar.swarm is False
    toggle_violin(app._ctx)
    assert bar.violin is False


def test_persistence_round_trip_through_slice(viewer_app):
    app = viewer_app
    app._app_state.bar.swarm = True
    app._app_state.bar.violin = False
    snap = vs.snapshot(app._ctx)
    bar_snap = snap["tabs"]["Bar Plots"]      # per-tab sub-dict
    assert bar_snap["swarm"] is True and bar_snap["violin"] is False

    # Flip the live slice, then apply the saved bar state — the slice must follow.
    app._app_state.bar.swarm = False
    vs._apply_bar(app, bar_snap)
    assert app._app_state.bar.swarm is True
    assert app._app_state.bar.violin is False
