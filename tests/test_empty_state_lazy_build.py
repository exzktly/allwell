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

from PySide6.QtWidgets import QLabel, QStackedWidget

#: Spelled out rather than imported from ``core.ui_helpers``. This test has to be
#: runnable against the code *before* the fix to prove it catches anything, and
#: importing a constant the fix introduces turns that run into an ImportError —
#: which looks like a red run while measuring nothing at all.
NO_DATASET_TITLE = "Load a directory."


def _stack_pages(app) -> list[int]:
    return [s.currentIndex() for s in (getattr(app, "_plot_empty_stacks", None) or [])]


def _visible_placeholder_titles(app) -> list[str]:
    """The title text of every placeholder currently on screen."""
    out = []
    for stack in (getattr(app, "_plot_empty_stacks", None) or []):
        if stack.currentIndex() != 0:
            continue
        placeholder = stack.widget(0)
        labels = [lbl.text() for lbl in placeholder.findChildren(QLabel) if lbl.text()]
        out.extend(labels)
    return out


def test_a_lazily_built_tab_does_not_ask_for_a_dataset_that_is_loaded(loaded_app):
    """Open every lazy-only tab on an already-loaded app.

    The assertion is on the placeholder's *words*, not on its page index. A
    selection-gated tab legitimately sits on page 0 here — ``_load_path`` clears
    the well selection, so "no wells selected" is the honest thing to say. What
    must never appear is the no-dataset copy, which is what Spatial and smFISH
    showed while the rail header named the loaded dataset.
    """
    app = loaded_app
    assert app._app_state.dataset.well_paths, "fixture should have loaded a dataset"

    lazy_titles = [d.name for d in app._features.top_level() if d.lazy_only]
    assert lazy_titles, "the registry should mark some tabs lazy_only"

    for title in lazy_titles:
        app._notebook.setCurrentByName(title)
        app._drain_pending_centre_builders()

    assert _stack_pages(app), "opening the lazy tabs should have registered stacks"
    shown = _visible_placeholder_titles(app)
    assert NO_DATASET_TITLE not in shown, (
        f"a placeholder still asks for a dataset that is loaded — showing {shown}, "
        f"lazy tabs opened={lazy_titles}"
    )


def test_an_image_browser_subtab_does_not_ask_for_a_dataset_either(loaded_app):
    """Image Browser's sub-tabs build through their *own* funnel.

    ``build_image_browser_subtab`` does not go through ``_build_pending``, so the
    refresh has to be repeated there. This was safe for exactly as long as no
    sub-tab registered an empty-state stack — the moment Cell Browser got one it
    reproduced the original bug in a new place, showing "Load a directory." over
    a loaded dataset.
    """
    from review.features.image_browser.controller import show_image_browser_subtab

    app = loaded_app
    assert app._app_state.dataset.well_paths

    for sub in ("Image Table", "Cell Browser", "Segmentation", "Playground"):
        show_image_browser_subtab(app._ctx, sub)

    shown = _visible_placeholder_titles(app)
    assert NO_DATASET_TITLE not in shown, (
        f"an Image Browser sub-tab asks for a dataset that is loaded — {shown}"
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
