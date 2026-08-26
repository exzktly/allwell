"""Each tab's Save menu acts on that tab's figure, not on whichever is visible.

``copy_active_card_as_svg`` and ``save_active_card_figure`` resolved "the first
visible plot card" from a fixed roster. That is right for a single global
toolbar action, and wrong for the eight tab-local Save dropdowns wired to them:
in Split View two cards are visible at once, so Bar Plots' own "Copy SVG"
copied whichever card came first in the roster — the line figure.

``save_card_to_gallery``, the third item in the same menu, had already been
given the card's registry name for exactly this reason, and
``plot_card_actions``' own docstring described the hazard while fixing one of
the three.

**The rule is unchanged; where it is enforced moved.** The eight tabs no longer
call those three actions themselves — ``core.ui_helpers.build_plot_save_menu``
wires all three from the one ``card_key`` its caller passes, which is a
stronger guarantee than three call sites agreeing by hand. So the scan surface
is now the builder plus each tab's ``card_key`` argument. Repointing it is the
point: a guard whose scan surface stops covering the code it is about reports
green for a rule nobody is following (CLAUDE.md §7.5 #3) — or, as here, red for
a rule that is being followed better than before.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest
from tests._repo_scan import parse_source

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_ACTIONS = {"copy_active_card_as_svg", "save_active_card_figure",
            "save_card_to_gallery"}

_BUILDER = "well_viewer/core/ui_helpers.py"

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


def _builder_body() -> ast.FunctionDef:
    tree = parse_source((_ROOT / _BUILDER))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "build_plot_save_menu")


def _save_menu_calls(rel: str) -> list[ast.Call]:
    tree = parse_source((_ROOT / rel))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "build_plot_save_menu"]


def test_the_builder_wires_all_three_items_from_one_card_key():
    """All three, and each from the *same* name — not one hard-coded card."""
    builder = _builder_body()
    wired: dict[str, set[str]] = {}
    for node in ast.walk(builder):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _ACTIONS):
            continue
        args = [ast.unparse(a) for a in node.args[1:]]
        wired.setdefault(node.func.id, set()).update(args or {""})

    assert set(wired) == _ACTIONS, (
        f"the Save-menu builder wires {sorted(wired)}; expected all three items"
    )
    names = set().union(*wired.values())
    assert names == {"card_key"}, (
        f"the three items act on {sorted(names)}; they must all take the "
        "caller's card_key, or in Split View one of them reaches another tab's "
        "figure"
    )


@pytest.mark.parametrize("rel", _TAB_VIEWS)
def test_every_plot_tab_names_its_own_card(rel):
    calls = _save_menu_calls(rel)
    assert calls, f"{rel} builds no Save menu"
    for call in calls:
        keys = [kw for kw in call.keywords if kw.arg == "card_key"]
        assert keys, (
            f"{rel}:{call.lineno} builds a Save menu without naming a card, so "
            "its items would act on 'the first visible card'"
        )
        value = keys[0].value
        assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
            f"{rel}:{call.lineno} passes a computed card_key; it must be the "
            "tab's own registry name, readable here"
        )
        assert value.value.startswith("_"), (
            f"{rel}:{call.lineno} card_key {value.value!r} is not a registry name"
        )


@pytest.mark.parametrize("rel", _TAB_VIEWS)
def test_no_tab_still_wires_a_card_action_by_hand(rel):
    """A tab reaching past the builder is how the three could disagree again."""
    tree = parse_source((_ROOT / rel))
    stray = sorted({node.func.id for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in _ACTIONS})
    assert not stray, (
        f"{rel} calls {stray} directly instead of through build_plot_save_menu"
    )


def test_the_global_action_still_falls_back_to_the_visible_card():
    """The no-name path is the one a toolbar button that belongs to no tab
    uses, and it has to keep working."""
    from well_viewer.core import plot_card_actions

    sig = inspect.signature(plot_card_actions.copy_active_card_as_svg)
    assert sig.parameters["card_attr"].default is None
    src = inspect.getsource(plot_card_actions._active_card)
    assert "isVisible()" in src, "the fallback path is gone"
