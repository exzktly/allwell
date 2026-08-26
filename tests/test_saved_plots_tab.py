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
    ctx = SimpleNamespace(app=app, state=app._app_state,
                          widgets=WidgetRegistry(app), runtime=AppRuntime(app))
    build_saved_plots_tab(ctx, parent)

    # No saved plots → empty-state page, regardless of dataset presence.
    assert app._saved_plots_stack.currentIndex() == 0

    # Save one, repopulate (what opening the tab does) → gallery page shows
    # immediately, without needing an app restart.
    get_store(ctx).add(ctx, _rec(), b"not-a-real-png")
    populate_saved_plots_grid(ctx)

    assert app._saved_plots_stack.currentIndex() == 1
    assert "1 saved plot" in app._saved_plots_count_lbl.text()


def test_the_gallery_can_be_opened_at_one_particular_card(qapp, tmp_path):
    """Home's thumbnails send the user here by plot id, and landing at the top
    of the list is indistinguishable, from the page, from the link not working.

    Driven through the real builder rather than by handing the function a
    dictionary: the index it reads is written by ``populate_saved_plots_grid``,
    and an index that were never populated — or populated under a different key
    — would pass every test made against a fixture.
    """
    from well_viewer.features.saved_plots.tab_view import scroll_to_saved_plot

    app = _app(tmp_path)
    parent = QWidget()
    ctx = SimpleNamespace(app=app, state=app._app_state,
                          widgets=WidgetRegistry(app), runtime=AppRuntime(app))
    build_saved_plots_tab(ctx, parent)
    store = get_store(ctx)
    for pid in ("r1", "r2", "r3"):
        rec = _rec()
        rec.id, rec.png_filename = pid, f"{pid}.png"
        store.add(ctx, rec, b"not-a-real-png")
    populate_saved_plots_grid(ctx)

    assert scroll_to_saved_plot(ctx, "r2") is True
    card = ctx.runtime.saved_plots.cards["r2"]
    assert card.property("focusflash") == "true"

    # A plot that is not in the gallery is reported, not silently ignored — the
    # caller turns that into a message rather than leaving the user on a page
    # that looks like it did nothing.
    assert scroll_to_saved_plot(ctx, "never-saved") is False


def test_the_card_index_is_rebuilt_rather_than_accumulated(qapp, tmp_path):
    """``clear_layout`` destroys the previous cards' C++ objects.

    An index that kept the old entries would hand out handles to deleted
    widgets, and touching one raises from inside a Qt slot — where it surfaces as
    a crash with no obvious connection to the delete that caused it.
    """
    app = _app(tmp_path)
    parent = QWidget()
    ctx = SimpleNamespace(app=app, state=app._app_state,
                          widgets=WidgetRegistry(app), runtime=AppRuntime(app))
    build_saved_plots_tab(ctx, parent)
    store = get_store(ctx)
    store.add(ctx, _rec(), b"not-a-real-png")
    populate_saved_plots_grid(ctx)
    assert set(ctx.runtime.saved_plots.cards) == {"r1"}

    store.delete(ctx, "r1")
    populate_saved_plots_grid(ctx)
    assert ctx.runtime.saved_plots.cards == {}
