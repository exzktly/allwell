"""A tab body built after a dataset load must not show the no-dataset placeholder.

``wrap_with_empty_state`` registers its stack on page 0 — "Load a directory." —
and ``refresh_plot_empty_states`` is what flips it to the tab's real body. A tab
marked ``lazy_only`` in the feature registry does not build at startup or during
the post-load drain; it builds the first time the user clicks it, which is
*after* the load-time refresh has already run over the stacks that existed then.
So its stack stayed on page 0 for the life of the session, and Spatial and smFISH
told the user to load a directory while the rail header named the loaded dataset
and the plate showed its wells lit.

Multivariate is ``lazy_only`` too and did not show the bug, which is the tell:
its controller calls ``refresh_plot_empty_states`` itself. The fix puts that call
in the one place every lazy build funnels through, so no feature has to remember.
"""
from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget


def _stack_pages(app) -> list[int]:
    return [s.currentIndex() for s in (getattr(app, "_plot_empty_stacks", None) or [])]


def test_a_lazily_built_tab_shows_its_body_not_the_no_dataset_placeholder(loaded_app):
    """Open every lazy-only tab on an already-loaded app; none may sit on page 0."""
    app = loaded_app
    assert app._app_state.dataset.well_paths, "fixture should have loaded a dataset"

    lazy_titles = [d.name for d in app._features.top_level() if d.lazy_only]
    assert lazy_titles, "the registry should mark some tabs lazy_only"

    for title in lazy_titles:
        app._notebook.setCurrentByName(title)
        app._drain_pending_centre_builders()

    pages = _stack_pages(app)
    assert pages, "opening the lazy tabs should have registered empty-state stacks"
    assert all(page == 1 for page in pages), (
        "a stack is still on the 'Load a directory.' placeholder with a dataset "
        f"loaded — pages={pages}, lazy tabs opened={lazy_titles}"
    )


def test_the_placeholder_still_shows_before_any_dataset_is_loaded(viewer_app):
    """The other direction: the fix must not flip stacks on with no dataset.

    A refresh that unconditionally showed the body would 'fix' this bug by
    replacing one wrong state with another — a blank canvas where the
    placeholder belongs.
    """
    app = viewer_app
    assert not app._app_state.dataset.well_paths

    app._drain_pending_centre_builders()
    pages = _stack_pages(app)
    assert pages, "the drain should have registered empty-state stacks"
    assert all(page == 0 for page in pages), (
        f"no dataset is loaded, so every stack should show the placeholder — {pages}"
    )

    stacks = app._plot_empty_stacks
    assert all(isinstance(s, QStackedWidget) for s in stacks), (
        "_plot_empty_stacks must stay a list of stacks — existing tests index it directly"
    )
