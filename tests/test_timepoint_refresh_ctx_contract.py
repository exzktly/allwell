"""Regression: the plotting tabs' timepoint-refresh handlers take ``ctx``.

A8-teardown T3 flipped ``refresh_distribution_timepoints`` and
``refresh_heatmap_timepoints`` to take ``ctx`` (combo via ``ctx.widgets``,
timepoints via the ctx leaves, split-mode via ``ctx.runtime.split``). Their
channel-change callers in ``features/registry.py`` pass ``app._ctx``. Those
callers wrap the refresh in a broad ``except Exception`` and only log — so a
bare-``app`` argument would raise ``AttributeError`` on ``app.widgets`` and be
silently swallowed, leaving the timepoint dropdown quietly un-refreshed on
every channel change. These tests call the handlers with a real ``ctx``
(empty dataset → no data access, no Qt render) and assert they populate the
widgets, locking the ctx contract so a regression fails loudly.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QComboBox

from review.core.app_runtime import AppRuntime
from review.core.widget_registry import WidgetRegistry


def _ctx(widgets_map):
    app = SimpleNamespace()
    reg = WidgetRegistry(app)
    for name, widget in widgets_map.items():
        reg.register(name, widget)
    state = SimpleNamespace(
        # Empty well set → get_all_timepoints() returns [] without ever
        # reaching the god-object _get_rows row-fetch.
        dataset=SimpleNamespace(well_paths={}),
        plot=SimpleNamespace(focus_timepoint=None),
        # Heat Map slice: refresh_heatmap_timepoints stamps tp_values here (the
        # session-transient timepoint list moved off the runtime namespace).
        heatmap=SimpleNamespace(tp_values=[]),
    )
    ctx = SimpleNamespace(
        app=app,
        widgets=reg,
        state=state,
        runtime=AppRuntime(app),
        bus=SimpleNamespace(),
    )
    app._ctx = ctx
    return ctx


def test_refresh_distribution_timepoints_takes_ctx(qtbot):
    from review.features.distribution.tab_view import (
        refresh_distribution_timepoints,
    )

    cb = QComboBox()
    ctx = _ctx({"_distribution_tp_cb": cb})
    refresh_distribution_timepoints(ctx)  # must not raise
    # Empty dataset → the "no timepoints" placeholder.
    assert cb.count() == 1
    assert cb.itemText(0) == "—"


def test_refresh_heatmap_timepoints_takes_ctx(qtbot):
    from review.features.heatmap.tab_view import refresh_heatmap_timepoints

    cb = QComboBox()
    ctx = _ctx({"_heatmap_tp_cb": cb})
    # No _heatmap_ax registered → the onward _redraw no-ops (returns early),
    # so this exercises the ctx contract without a real render.
    refresh_heatmap_timepoints(ctx)  # must not raise
    # Empty dataset → the "no timepoints" placeholder.
    assert cb.count() == 1
    assert cb.itemText(0) == "—"
    # The timepoint-value cache now lives on the typed Heat Map state slice
    # (moved off the runtime namespace so persistence can read it via ctx.state).
    assert ctx.state.heatmap.tp_values == []
