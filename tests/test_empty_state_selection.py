"""A page with a dataset but no wells picked says so, in one shared place.

These assert at the **stack boundary** — what is actually on screen — rather than
on the predicate behind it. A test that reads ``has_plottable_selection`` passes
happily while the placeholder stays behind the figure, which is the failure mode
worth guarding: the bug being fixed here was never a wrong computation, it was a
correct one that never reached the screen.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

NO_DATASET_TITLE = "Load a directory."
NO_SELECTION_TITLE = "No wells selected."


def _placeholder_titles(app) -> list[str]:
    """Title text of every placeholder currently showing."""
    out = []
    for stack in (getattr(app, "_plot_empty_stacks", None) or []):
        if stack.currentIndex() != 0:
            continue
        out.extend(lbl.text() for lbl in stack.widget(0).findChildren(QLabel) if lbl.text())
    return out


def _open(app, page: str) -> None:
    assert app._notebook.setCurrentByName(page), f"could not open {page}"
    app._drain_pending_centre_builders()


@pytest.mark.slow
def test_clearing_the_selection_shows_the_shared_placeholder(loaded_app):
    """Plot pages swap to the placeholder when the last well is deselected."""
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app
    _open(app, "Plotting")

    app._set_selected_wells(set(WELLS), commit=True)
    assert NO_SELECTION_TITLE not in _placeholder_titles(app), (
        "wells are selected, so no page should be asking for a selection"
    )

    app._set_selected_wells(set(), commit=True)
    titles = _placeholder_titles(app)
    assert NO_SELECTION_TITLE in titles, (
        f"clearing the selection should raise the shared placeholder — showing {titles}"
    )
    # The dataset is still loaded. Saying otherwise is the contradiction that
    # started this work.
    assert NO_DATASET_TITLE not in titles, titles


@pytest.mark.slow
def test_selecting_wells_again_puts_the_figure_back(loaded_app):
    """The swap runs both ways — a placeholder that never retracts is worse."""
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app
    _open(app, "Plotting")
    app._set_selected_wells(set(), commit=True)
    assert NO_SELECTION_TITLE in _placeholder_titles(app)

    app._set_selected_wells({WELLS[0]}, commit=True)
    assert NO_SELECTION_TITLE not in _placeholder_titles(app), (
        "one well is selected, so the figure should be back"
    )


@pytest.mark.slow
def test_a_background_page_is_updated_too(loaded_app):
    """The refresh covers every registered stack, not just the visible tab.

    ``apply_selection_refresh`` only refreshes ``_visible_tabs``, and a feature
    with an ``on_selection_changed`` hook never reaches the plot redraw where the
    other empty-state refresh lives — so a page the user was not looking at when
    the selection changed would show the previous answer on their return.
    """
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app
    _open(app, "Review CSV")          # build it, so its stack is registered
    _open(app, "Plotting")            # ...then look away
    app._set_selected_wells(set(WELLS), commit=True)
    app._set_selected_wells(set(), commit=True)

    _open(app, "Review CSV")
    assert NO_SELECTION_TITLE in _placeholder_titles(app), (
        "Review CSV was in the background when the selection cleared and still "
        "shows the state it had when it was last visible"
    )


@pytest.mark.slow
def test_heat_map_is_not_gated_on_the_selection(loaded_app):
    """A *populated* Heat Map must keep drawing when the plate is cleared.

    The fixture's active layout is the empty ``Sidebar`` one the sidebar seeds,
    so this seeds a real layout first. Branching on whether the layout happened
    to have cells — which the first version of this test did — meant the case it
    is named for never ran.
    """
    from well_viewer.features.heatmap.controller import active_layout, heatmap_empty_reason
    from well_viewer.features.heatmap.models import make_plate_layout

    app = loaded_app
    ctx = app._ctx
    _open(app, "Plotting")
    app._plotting_notebook.setCurrentByName("Heat Map")
    app._drain_pending_centre_builders()

    populated = make_plate_layout(ctx.state.dataset.well_paths.keys())
    assert populated.cells, "the fixture's wells should populate a plate layout"
    ctx.runtime.heatmap.layouts = [populated]
    ctx.state.heatmap.active_layout_name = populated.name
    assert active_layout(ctx).cells

    app._set_selected_wells(set(), commit=True)
    assert heatmap_empty_reason(ctx) is None, (
        "the layout holds wells, so clearing the plate must not blank the map"
    )


@pytest.mark.slow
def test_heat_map_placeholder_retracts_when_its_layout_is_filled(loaded_app):
    """Its own instruction has to work.

    The placeholder reads "click Auto-populate from selection". Doing exactly
    that filled the layout and left the placeholder standing, because nothing
    re-evaluated the reason — the reason depends on state only this feature
    writes, so none of the app-wide refresh triggers fire for it.
    """
    from PySide6.QtWidgets import QStackedWidget

    from well_viewer.features.heatmap.controller import active_layout
    from well_viewer.features.heatmap.sidebar_view import _on_autopopulate
    from tests.fixtures.mini_dataset import WELLS

    app = loaded_app
    ctx = app._ctx
    _open(app, "Plotting")
    app._plotting_notebook.setCurrentByName("Heat Map")
    app._drain_pending_centre_builders()

    canvas = ctx.widgets.get_or_none("_heatmap_canvas")
    stack = canvas
    while stack is not None and not (
        isinstance(stack, QStackedWidget) and stack.objectName() == "PlotEmptyStateStack"
    ):
        stack = stack.parentWidget()
    assert stack is not None, "the heat map canvas is not inside an empty-state stack"

    if active_layout(ctx).cells:                      # nothing to prove
        return
    assert stack.currentIndex() == 0

    app._set_selected_wells(set(WELLS), commit=True)
    _on_autopopulate(ctx)
    assert active_layout(ctx).cells, "auto-populate should have filled the layout"
    assert stack.currentIndex() == 1, (
        "the layout is populated, so the placeholder that told the user to "
        "populate it must be gone"
    )


@pytest.mark.slow
def test_spatial_placeholder_retracts_at_summary_level(loaded_app):
    """Spatial's reason is level-dependent, so a level change must re-run it.

    Summary falls back to every loaded well, so it has something to draw with no
    selection — the reason returns None — but nothing re-evaluated it on the
    level change and the placeholder stayed over a populated page.
    """
    from PySide6.QtWidgets import QStackedWidget

    from well_viewer.features.spatial import controller as sp
    from well_viewer.features.spatial import state as sp_state

    app = loaded_app
    ctx = app._ctx
    _open(app, "Spatial")
    app._set_selected_wells(set(), commit=True)

    canvas = ctx.widgets.get_or_none("_sp_canvas")
    stack = canvas
    while stack is not None and not (
        isinstance(stack, QStackedWidget) and stack.objectName() == "PlotEmptyStateStack"
    ):
        stack = stack.parentWidget()
    assert stack is not None, "the spatial canvas is not inside an empty-state stack"
    assert stack.currentIndex() == 0, "per-frame level with no selection: placeholder"

    sp.sp_set_level(ctx, sp_state.LEVEL_SUMMARY)
    assert sp.spatial_empty_reason(ctx) is None
    assert stack.currentIndex() == 1, (
        "Summary draws every loaded well, so its placeholder must have retracted"
    )
