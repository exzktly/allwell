"""Saved selections are a feature of their own, not part of the rail's builder.

About 400 of ``review/core/sidebar_view.py``'s 525 lines were the Saved
selections drawer — add, load, delete, the replicate-visibility plan, the Image
Table sync — inside the module that builds the rail's main frame. They live in
``review/core/saved_selections.py``; the builder only places the drawer. This
keeps them from growing back: the builder defines the frame and nothing that
reads or writes ``ctx.state.selection.plot_groups``.
"""

from __future__ import annotations

import ast

from tests._repo_scan import REPO_ROOT, parse_source


def _defs(rel: str) -> set[str]:
    tree = parse_source(REPO_ROOT / rel)
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def test_the_rail_builder_defines_no_saved_selection_logic():
    leaked = {n for n in _defs("review/core/sidebar_view.py")
              if "plot_group" in n or "saved_selection" in n}
    assert not leaked, f"Saved selections code back in sidebar_view: {sorted(leaked)}"


def test_the_saved_selections_module_owns_the_drawer():
    defs = _defs("review/core/saved_selections.py")
    for name in ("build_plot_groups_drawer", "refresh_plot_groups_sidebar",
                 "plot_group_visibility_plan", "_plot_groups_add_current",
                 "_plot_groups_load", "_plot_groups_delete"):
        assert name in defs, name
