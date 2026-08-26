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

    # No assertion here about *where* it scrolled: this test's parent is never
    # shown or sized, so the scroll area has no range and any such check would
    # read 0 == 0 and pass against code that scrolls nowhere. The landing is
    # pinned by ``test_the_gallery_scrolls_to_a_card_even_when_the_page_was_just_shown``,
    # which shows and sizes its parent and uses decodable images.

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


def test_the_gallery_scrolls_to_a_card_even_when_the_page_was_just_shown(
        qapp, tmp_path):
    """The defect this exists for is the whole feature failing silently.

    Home's thumbnails call this immediately after switching to the Saved Plots
    page, so the scroll area has no geometry yet: its range is zero and every
    card sits at y=0. A scroll issued then is a scroll to the top of a list that
    has not been laid out — which is what shipped, and it always landed on the
    first card while reporting success, because `ensureWidgetVisible` returns
    nothing at all.
    """
    from PySide6.QtWidgets import QStackedWidget
    from well_viewer.features.saved_plots.tab_view import scroll_to_saved_plot

    app = _app(tmp_path)
    parent = QWidget()
    parent.resize(900, 700)
    from PySide6.QtWidgets import QVBoxLayout
    QVBoxLayout(parent)
    stack = QStackedWidget(parent)
    parent.layout().addWidget(stack)
    elsewhere = QWidget()
    page = QWidget()
    stack.addWidget(elsewhere)
    stack.addWidget(page)
    stack.setCurrentWidget(elsewhere)          # the user is on another page

    ctx = SimpleNamespace(app=app, state=app._app_state,
                          widgets=WidgetRegistry(app), runtime=AppRuntime(app))
    build_saved_plots_tab(ctx, page)
    store = get_store(ctx)
    for pid in ("a", "b", "c", "d", "e", "f"):
        rec = _rec()
        rec.id, rec.png_filename, rec.title = pid, f"{pid}.png", f"Fig {pid}"
        store.add(ctx, rec, _real_png())
    parent.show()
    qapp.processEvents()

    # Exactly what focus_saved_plot does: switch page, repopulate, scroll — all
    # in one turn of the event loop, before anything has been laid out.
    stack.setCurrentWidget(page)
    populate_saved_plots_grid(ctx)
    assert scroll_to_saved_plot(ctx, "a") is True     # oldest → bottom of the list
    for _ in range(20):
        qapp.processEvents()

    scroll = ctx.widgets.get_or_none("_saved_plots_scroll")
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0, "the gallery should be taller than its viewport"
    assert bar.value() > 0, "the view is still at the top of the list"
    card = ctx.runtime.saved_plots.cards["a"]
    top = card.mapTo(scroll.widget(), card.rect().topLeft()).y()
    assert bar.value() == max(bar.minimum(), min(bar.maximum(), top - 8))


def _real_png() -> bytes:
    """A decodable PNG, so the cards get real heights to scroll past."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import numpy as np

    buf = io.BytesIO()
    mpimg.imsave(buf, (np.linspace(0, 1, 60 * 80 * 3).reshape(60, 80, 3) * 255)
                 .astype("uint8"), format="png")
    return buf.getvalue()
