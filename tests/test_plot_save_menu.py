"""Every plot tab's Save menu is built by the one builder, and offers the cells.

Eight tabs assembled this menu by hand and drifted — six "Save Figure…" against
two "Save figure…", seven "Export CSV…" against one "Export Summary CSV…". A
menu written per tab is also a menu that can *miss* an entry per tab, and the
raw-cell export is the entry a user would never find missing until they looked
for it on the one tab that lacked it.

So the rule is structural rather than editorial: a plot tab does not assemble a
save menu, it calls ``core.ui_helpers.build_plot_save_menu``, which requires the
cells callback. These hold that.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FEATURES = REPO_ROOT / "well_viewer" / "features"

#: The tab builders that draw a plot and therefore owe a Save menu.
PLOT_TAB_VIEWS = (
    "barplot/tab_view.py",
    "distribution/tab_view.py",
    "heatmap/tab_view.py",
    "lineplot/tab_view.py",
    "multivariate/tab_view.py",
    "scatter/agg_tab_view.py",
    "scatter/cells_tab_view.py",
    "spatial/tab_view.py",
)


def _calls(tree, name: str):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _callee(n) == name]


def _callee(call) -> str:
    func = call.func
    return getattr(func, "id", "") or getattr(func, "attr", "")


@pytest.mark.parametrize("relative", PLOT_TAB_VIEWS)
def test_every_plot_tab_builds_its_save_menu_through_the_builder(relative):
    tree = ast.parse((FEATURES / relative).read_text())
    assert _calls(tree, "build_plot_save_menu"), (
        f"{relative} does not call build_plot_save_menu. Assembling the menu by "
        "hand is how the eight tabs drifted apart, and how one of them would "
        "quietly lack the raw-cell export."
    )


@pytest.mark.parametrize("relative", PLOT_TAB_VIEWS)
def test_every_plot_tab_offers_the_raw_cells(relative):
    tree = ast.parse((FEATURES / relative).read_text())
    for call in _calls(tree, "build_plot_save_menu"):
        names = {kw.arg for kw in call.keywords}
        assert "cells_cb" in names, (
            f"{relative}:{call.lineno} builds a Save menu without a cells "
            "callback — every plot can hand back the cells it was computed from."
        )
        assert "card_key" in names and "data_cb" in names


@pytest.mark.parametrize("relative", PLOT_TAB_VIEWS)
def test_no_plot_tab_still_hand_rolls_an_export_menu(relative):
    """``make_action_menu_button`` is fine for other menus; not for this one.

    Matched with *and* without the caret, because ``_with_caret`` appends one —
    so ``make_action_menu_button("Save", …)`` renders identically to
    ``"Save ▾"`` and a scan for the caret alone would be trivially evaded.
    """
    tree = ast.parse((FEATURES / relative).read_text())
    for call in _calls(tree, "make_action_menu_button"):
        if not call.args:
            continue
        first = call.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        label = first.value.replace("▾", "").strip().lower()
        assert label not in ("save", "export"), (
            f"{relative}:{call.lineno} builds a save/export menu by hand — "
            "use build_plot_save_menu."
        )


def test_the_builder_requires_the_cells_callback():
    """Not merely "the call sites pass one" — the signature must demand it.

    A default of ``None`` would make the entry optional again, and the next tab
    added would be the one without it.
    """
    import inspect

    from well_viewer.core.ui_helpers import build_plot_save_menu

    parameter = inspect.signature(build_plot_save_menu).parameters["cells_cb"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_menu_reads_as_one_vocabulary():
    """One verb, and no leftover "Export" inside a menu the button calls Save."""
    from well_viewer.core import ui_helpers

    tree = ast.parse(pathlib.Path(ui_helpers.__file__).read_text())
    builder = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "build_plot_save_menu")
    labels = [call.args[1].value for call in _calls(builder, "add_menu_action")
              if len(call.args) > 1 and isinstance(call.args[1], ast.Constant)]
    assert labels == ["Save figure…", "Copy figure as SVG", "Save to Saved Plots"]
    button = _calls(builder, "make_action_menu_button")[0]
    assert button.args[0].value == "Save ▾"
    assert not any("export" in str(label).lower() for label in labels), (
        "the button says Save; an entry inside it saying Export is the drift "
        "this builder exists to end")
