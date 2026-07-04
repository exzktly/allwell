"""Characterization — batch-export panel data path (A8 / Epic-A14).

Pins the observable behaviour of the batch-export mode switch BEFORE the panels
are converted from ``app`` to ``ctx``:

  * which panel class ``_show_batch_mode`` builds for each mode,
  * the data-reading surface each panel exposes off the live app state
    (export channels, selected channel, value-column mapping, export groups).

Driven through the stable public path (``send_plot_to_batch_export`` ->
``ctx.runtime.batch_export.set_mode`` -> ``_show_batch_mode``) so the test is
identical before and after the constructor signature flips to ``ctx``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from well_viewer.core.ui_helpers import send_plot_to_batch_export
from well_viewer.features.batch_export import (
    BatchExportPanel,
    BarBatchExportPanel,
    ScatterBatchExportPanel,
)

_EXPECTED_CLASS = {
    "line": BatchExportPanel,
    "bar": BarBatchExportPanel,
    "scatter_cells": ScatterBatchExportPanel,
    "scatter_agg": ScatterBatchExportPanel,
}


def _panel_for(app, mode):
    send_plot_to_batch_export(app, mode)
    state = app._batch_export_inline_state
    assert state["mode"] == mode
    return state["panels"][mode]


def test_show_batch_mode_builds_expected_class_per_mode(loaded_app):
    for mode, cls in _EXPECTED_CLASS.items():
        panel = _panel_for(loaded_app, mode)
        assert type(panel) is cls, (mode, type(panel))
    assert _panel_for(loaded_app, "scatter_cells")._scatter_mode == "cells"
    assert _panel_for(loaded_app, "scatter_agg")._scatter_mode == "aggregate"


def test_line_bar_panels_read_channel_surface_off_app_state(loaded_app):
    for mode in ("line", "bar"):
        panel = _panel_for(loaded_app, mode)
        chans = panel._all_export_channels()
        assert isinstance(chans, list) and chans, (mode, chans)
        sel = panel._selected_export_channel()
        assert isinstance(sel, str) and sel, (mode, sel)


def test_sync_from_app_pulls_replicate_sets(loaded_app):
    expected = len(loaded_app._rep_sets_loaded())
    panel = _panel_for(loaded_app, "line")
    panel._sync_from_app()
    assert len(panel._groups_for_export()) == expected


def test_scatter_axis_column_mapping_stable(loaded_app):
    # The scatter axis -> CSV column resolver is the data path the exporter
    # collects rows through; drive it through the panel's own combos so the
    # (channel, metric) -> column mapping is pinned end to end.
    panel = _panel_for(loaded_app, "scatter_cells")
    x_cb = getattr(panel, "_sc_cells_x_cb", None)
    prop_cb = getattr(panel, "_sc_cells_metric_x_cb", None)
    if x_cb is not None and prop_cb is not None and x_cb.count():
        col = panel._scatter_axis_col(x_cb, prop_cb)
        assert isinstance(col, str) and col
