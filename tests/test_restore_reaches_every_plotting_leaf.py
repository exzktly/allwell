"""Restore navigates to any leaf the registry puts inside the Plotting stack.

``view_state._activate_tab`` is the one router a restored view goes through —
Saved Plots' Restore, and the session snapshot replayed at startup. A leaf that
lives inside the Plotting stack has to be asked for on the *plotting* notebook;
asking the outer notebook for it selects nothing, so the settings are applied
and the user is left looking at whichever tab they were already on.

That is what a hand-written roster of five leaf names produced when a sixth was
added: Response Curves restored its channel, its timepoints and its fit, and
never appeared. The rule is therefore stated against the registry — the same
source ``core/centre_view`` builds the stack from — rather than against a list
this file also has to remember to update.
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.features.registry import build_feature_registry
from well_viewer.persistence import view_state


class _Notebook:
    """Records what was asked for, and whether it has a page by that name."""

    def __init__(self, pages=()):
        self.pages = list(pages)
        self.asked: list[str] = []

    def setCurrentByName(self, name):  # Qt-style name, as the real one has
        self.asked.append(str(name))

    def pageNames(self):
        return list(self.pages)


def _ctx_with(leaves):
    outer = _Notebook(pages=["Home", "Plotting", "Image Browser"])
    inner = _Notebook(pages=list(leaves))
    window = SimpleNamespace(_notebook=outer, _plotting_notebook=inner)
    features = SimpleNamespace(sub_leaves=lambda parent: list(leaves)
                               if parent == "Plotting" else [])
    return SimpleNamespace(window=window, features=features), outer, inner


def _plotting_leaves() -> list[str]:
    return [d.name for d in build_feature_registry().all()
            if d.parent == "Plotting"]


def test_the_registry_declares_the_leaves_this_guard_measures():
    """A lookup that found nothing would pass every check below in silence."""
    leaves = _plotting_leaves()
    assert len(leaves) >= 5 and "Response Curves" in leaves


def test_every_plotting_leaf_is_routed_through_the_plotting_stack():
    leaves = _plotting_leaves()
    unreachable = []
    for leaf in leaves:
        ctx, outer, inner = _ctx_with(leaves)
        view_state._activate_tab(ctx, leaf)
        # The outer notebook is asked for the parent page, the inner one for the
        # leaf. A leaf the router does not recognise is sent to the outer
        # notebook by name, where no such page exists — which is the failure:
        # nothing raises and nothing moves.
        if outer.asked != ["Plotting"] or inner.asked != [leaf]:
            unreachable.append(
                f"{leaf!r}: outer asked {outer.asked}, plotting stack asked "
                f"{inner.asked}")
    assert not unreachable, (
        "A leaf inside the Plotting stack is not routed into it by "
        "view_state._activate_tab, so restoring a plot saved on it applies the "
        "settings without ever showing the figure:\n  "
        + "\n  ".join(unreachable))


def test_a_context_with_no_registry_still_routes_the_known_leaves():
    """The literal fallback covers a headless ctx, which has no registry."""
    outer = _Notebook(pages=["Home", "Plotting"])
    inner = _Notebook(pages=list(view_state._PLOT_SUBTABS))
    ctx = SimpleNamespace(
        window=SimpleNamespace(_notebook=outer, _plotting_notebook=inner))
    view_state._activate_tab(ctx, "Bar Plots")
    assert outer.asked == ["Plotting"] and inner.asked == ["Bar Plots"]
