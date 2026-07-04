"""Saved Plots gallery tab: empty-state is driven by the saved-plot count."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget

from well_viewer.core.app_runtime import AppRuntime
from well_viewer.core.widget_registry import WidgetRegistry
from well_viewer.features.saved_plots.state import SavedPlot, get_store
from well_viewer.features.saved_plots.tab_view import (
    build_saved_plots_tab,
    populate_saved_plots_grid,
)

pytestmark = pytest.mark.qt


def _app(tmp_path: Path):
    ds = SimpleNamespace(data_dir=tmp_path, persistence_doc=None)
    return SimpleNamespace(_app_state=SimpleNamespace(dataset=ds))


def _rec():
    return SavedPlot(
        id="r1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
        title="Fig", png_filename="r1.png", params={}, thresholds={},
        summary="Tab: Bar Plots",
    )


def test_empty_state_until_a_plot_is_saved(qapp, tmp_path):
    app = _app(tmp_path)
    parent = QWidget()  # retain so Qt doesn't delete the child stack
    # A real ViewerContext always carries a WidgetRegistry (``ctx.widgets``); the
    # builder registers its handles through it and register() stamps ``app._x``.
    # Reuse the same ctx for the repopulate so the registry sees the built handles.
    ctx = SimpleNamespace(app=app, widgets=WidgetRegistry(app), runtime=AppRuntime(app))
    build_saved_plots_tab(ctx, parent)

    # No saved plots → empty-state page, regardless of dataset presence.
    assert app._saved_plots_stack.currentIndex() == 0

    # Save one, repopulate (what opening the tab does) → gallery page shows
    # immediately, without needing an app restart.
    get_store(app).add(app, _rec(), b"not-a-real-png")
    populate_saved_plots_grid(ctx)

    assert app._saved_plots_stack.currentIndex() == 1
    assert "1 saved plot" in app._saved_plots_count_lbl.text()
