"""Deleting a saved plot must not move the gallery's viewport.

The report: "deleting a figure on saved plots causes the app to jump to some
weird position". It is not the delete that moves the view — it is the rebuild
behind it. ``populate_saved_plots_grid`` destroys every card and builds a fresh
set, so for a few event-loop passes the scroll area holds a list of nearly no
height, Qt clamps the scroll bar into that vanished range, and the value it
lands on is what the user is left looking at. Measured on a gallery of twelve
plots, deleting one halfway down moved the view 2,418 px away from the card the
Delete button had been on.

Both tests assert the position of a *surviving* card relative to the viewport,
not the scroll bar's value: the value legitimately changes when content above
the viewport disappears, and it is where the picture ends up on screen that the
report is about.
"""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget

from review.core.app_runtime import AppRuntime
from review.core.widget_registry import WidgetRegistry
from review.features.saved_plots import controller as C
from review.features.saved_plots.state import SavedPlot, get_store
from review.features.saved_plots.tab_view import (
    build_saved_plots_tab,
    populate_saved_plots_grid,
)

pytestmark = pytest.mark.qt

#: Plots in the fixture gallery, oldest first. Enough that the list is several
#: viewports tall and a delete has somewhere to jump to.
_PLOT_COUNT = 12


def _real_png() -> bytes:
    """A decodable PNG, so the cards get real heights to scroll past."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import numpy as np

    buf = io.BytesIO()
    mpimg.imsave(buf, (np.linspace(0, 1, 60 * 80 * 3).reshape(60, 80, 3) * 255)
                 .astype("uint8"), format="png")
    return buf.getvalue()


def _gallery(qapp, tmp_path: Path):
    """A built, shown, populated gallery of ``_PLOT_COUNT`` saved plots.

    Returns (ctx, parent, scroll). The parent is handed back so the caller keeps
    it alive — Qt deletes the whole page with it.
    """
    app = SimpleNamespace(_app_state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path, persistence_doc=None)))
    ctx = SimpleNamespace(app=app, state=app._app_state,
                          widgets=WidgetRegistry(app), runtime=AppRuntime(app))
    parent = QWidget()
    parent.resize(900, 700)
    QVBoxLayout(parent)
    page = QWidget()
    parent.layout().addWidget(page)
    build_saved_plots_tab(ctx, page)

    store = get_store(ctx)
    png = _real_png()
    for i in range(_PLOT_COUNT):
        store.add(ctx, SavedPlot(
            id=f"p{i}", created_at="2026-01-01T00:00:00", tab="Bar Plots",
            title=f"Fig {i}", png_filename=f"p{i}.png", params={},
            thresholds={}, summary="Tab: Bar Plots"), png)
    parent.show()
    populate_saved_plots_grid(ctx)
    _settle(qapp)
    scroll = ctx.widgets.get_or_none("_saved_plots_scroll")
    assert scroll.verticalScrollBar().maximum() > 0, \
        "the fixture gallery must be taller than its viewport"
    return ctx, parent, scroll


def _settle(qapp) -> None:
    """Run the event loop until the gallery's layout has stopped moving."""
    for _ in range(40):
        qapp.processEvents()


def _y_in_viewport(ctx, scroll, plot_id: str) -> int:
    """Where *plot_id*'s card sits relative to the top of the visible area."""
    card = ctx.runtime.saved_plots.cards[plot_id]
    top = card.mapTo(scroll.widget(), card.rect().topLeft()).y()
    return top - scroll.verticalScrollBar().value()


def _visible_from_top(ctx, scroll) -> list[str]:
    """The gallery's cards, in order, from the one at the top of the view down."""
    bar = scroll.verticalScrollBar()
    host = scroll.widget()
    out = []
    for plot_id, card in ctx.runtime.saved_plots.cards.items():
        top = card.mapTo(host, card.rect().topLeft()).y()
        if top + card.height() > bar.value():
            out.append(plot_id)
    return out


def test_deleting_a_plot_leaves_the_view_where_it_was(qapp, tmp_path):
    """Delete a card the user can see, below the one at the top of the view."""
    ctx, parent, scroll = _gallery(qapp, tmp_path)
    bar = scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    _settle(qapp)

    on_screen = _visible_from_top(ctx, scroll)
    anchor, victim = on_screen[0], on_screen[1]
    before = _y_in_viewport(ctx, scroll, anchor)

    C.delete_saved_plot(ctx, victim)
    _settle(qapp)

    assert victim not in ctx.runtime.saved_plots.cards
    assert _y_in_viewport(ctx, scroll, anchor) == before, (
        "the card at the top of the view moved when a card below it was deleted")


def test_deleting_the_card_at_the_top_of_the_view_holds_what_is_above_it(
        qapp, tmp_path):
    """The common case: the user deletes the card they are looking at.

    Its own position cannot be preserved — it is gone — so what must hold still
    is everything above the gap, with the following card flowing up into it.
    Anchoring downwards instead (onto the card below the deleted one) passes a
    naive "the view did not move" check while dragging the whole list up by a
    card's height, which is the jump being reported.
    """
    ctx, parent, scroll = _gallery(qapp, tmp_path)
    bar = scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    _settle(qapp)

    order = list(ctx.runtime.saved_plots.cards)
    victim = _visible_from_top(ctx, scroll)[0]
    above = order[order.index(victim) - 1]
    assert order.index(victim) > 0, "need a card above the one being deleted"
    before = _y_in_viewport(ctx, scroll, above)

    C.delete_saved_plot(ctx, victim)
    _settle(qapp)

    assert victim not in ctx.runtime.saved_plots.cards
    assert _y_in_viewport(ctx, scroll, above) == before, (
        "the card above the deleted one moved")
