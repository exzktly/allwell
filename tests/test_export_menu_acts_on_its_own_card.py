"""Each tab's Export menu acts on that tab's figure, not on whichever is visible.

``copy_active_card_as_svg`` and ``save_active_card_figure`` resolved "the first
visible plot card" from a fixed roster. That is right for a single global
toolbar action, and wrong for the eight tab-local Export dropdowns wired to
them: in Split View two cards are visible at once, so Bar Plots' own "Copy SVG"
copied whichever card came first in the roster — the line figure.

``save_card_to_gallery``, the third item in the same menu, had already been
given the card's registry name for exactly this reason, and
``plot_card_actions``' own docstring described the hazard while fixing one of
the three. All three take a name now, and this guard holds the eight menus to
naming the same card in all three items.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_ACTIONS = {"copy_active_card_as_svg", "save_active_card_figure",
            "save_card_to_gallery"}

_TAB_VIEWS = [
    "well_viewer/features/scatter/cells_tab_view.py",
    "well_viewer/features/scatter/agg_tab_view.py",
    "well_viewer/features/lineplot/tab_view.py",
    "well_viewer/features/spatial/tab_view.py",
    "well_viewer/features/heatmap/tab_view.py",
    "well_viewer/features/barplot/tab_view.py",
    "well_viewer/features/multivariate/tab_view.py",
    "well_viewer/features/distribution/tab_view.py",
]


def _named_cards(rel: str) -> dict[str, set[str]]:
    """``{action: {card names it was called with}}`` for one tab view."""
    tree = ast.parse((_ROOT / rel).read_text(), filename=rel)
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _ACTIONS):
            continue
        names = {a.value for a in node.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        out.setdefault(node.func.id, set()).update(names or {""})
    return out


@pytest.mark.parametrize("rel", _TAB_VIEWS)
def test_every_export_action_names_a_card(rel):
    called = _named_cards(rel)
    assert set(called) == _ACTIONS, (
        f"{rel} wires {sorted(called)}; expected all three Export items"
    )
    unnamed = sorted(a for a, names in called.items() if "" in names)
    assert not unnamed, (
        f"{rel}: {unnamed} act on 'the first visible card', so in Split View "
        "they reach another tab's figure"
    )


@pytest.mark.parametrize("rel", _TAB_VIEWS)
def test_the_three_items_agree_on_which_card(rel):
    called = _named_cards(rel)
    names = set().union(*called.values())
    assert len(names) == 1, (
        f"{rel}'s Export menu names more than one card: {sorted(names)}"
    )


def test_the_global_action_still_falls_back_to_the_visible_card():
    """The no-name path is the one a toolbar button that belongs to no tab
    uses, and it has to keep working."""
    import inspect

    from well_viewer.core import plot_card_actions

    sig = inspect.signature(plot_card_actions.copy_active_card_as_svg)
    assert sig.parameters["card_attr"].default is None
    src = inspect.getsource(plot_card_actions._active_card)
    assert "isVisible()" in src, "the fallback path is gone"
