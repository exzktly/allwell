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
    """Heat Map draws its layout, so clearing the plate must not blank it.

    It reads the selection only to outline cells. Gating it the way the plot
    tabs are gated would hide a populated saved layout the moment the user
    cleared the plate — a worse bug than the blank grid it was meant to fix.
    """
    from well_viewer.features.heatmap.controller import active_layout, heatmap_empty_reason

    app = loaded_app
    ctx = app._ctx
    _open(app, "Plotting")
    app._plotting_notebook.setCurrentByName("Heat Map")
    app._drain_pending_centre_builders()

    layout = active_layout(ctx)
    app._set_selected_wells(set(), commit=True)
    if layout.cells:
        assert heatmap_empty_reason(ctx) is None, (
            "the layout holds wells, so an empty plate selection must still draw"
        )
    else:
        reason = heatmap_empty_reason(ctx)
        assert reason is not None and "heat map" in reason.title.lower(), (
            "an empty layout should say so rather than draw a blank grid"
        )
