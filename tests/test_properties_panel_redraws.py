"""Every figure the Properties panel opens on can be redrawn from it.

``test_properties_panel_keys`` closes the seam that decides whether the panel
*opens*. This one closes the seam that decides whether anything the panel does
reaches the screen.

Some Properties controls cannot work by restyling the figure that is already
drawn — a log toggle on a tab that picks its own scale from the data, a colour
mode that is chosen while the artists are built — so they call
``ExportStyleSidebar._redraw_bound_figure``, which dispatches on figure
identity. That dispatch was a hand-written roster of six figures written when
there were six, and the Properties panel opens on nine. The three it did not
name fell through to its final ``app._redraw()``, which repaints the **Line**
tab: a Properties edit on the Response tab repainted a different figure
entirely and returned ``True``, so the control reported success and the user
saw nothing change. That is how the Response tab's X log toggle came to do
nothing at all.

Two lists, opposite ends of the app, no shared symbol and no import between
them — §7.5 #10's roster problem exactly. So the roster is held against the
one list that says which figures this panel is reachable for.

Read from the syntax tree rather than by importing either module: the sidebar
pulls Qt and the whole feature graph, and this belongs in the fast lane.
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import parsed_sources

_PANEL_MODULE = "review/features/export/style.py"
_PANEL_FUNCTION = "open_export_style_panel"
_SIDEBAR_MODULE = "review/features/export/sidebar_view.py"
_DISPATCH_FUNCTION = "_redraw_bound_figure"
_FEATURE_ROSTER = "_FIGURE_OWNING_FEATURES"


def _tree(module: str) -> ast.Module:
    for path, tree in parsed_sources():
        if path.as_posix().endswith(module):
            return tree
    raise AssertionError(
        f"{module} not found — this guard is reading a module that has moved, "
        "so it is measuring nothing")


def _func(module: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"{module}::{name} not found — this guard is measuring nothing")


def _figures_the_panel_opens_on() -> set[str]:
    """The ``_*_fig`` registry names in ``open_export_style_panel``'s roster.

    Selected by the roster's shape — a dict of 3-tuples — for the reason
    ``test_properties_panel_keys`` gives: naming the variable would let a
    rename silently empty the list, and accepting every dict literal in the
    function lets an unrelated one vouch for a figure the roster lacks.
    """
    for node in ast.walk(_func(_PANEL_MODULE, _PANEL_FUNCTION)):
        if not isinstance(node, ast.Dict) or not node.keys:
            continue
        if not all(isinstance(v, ast.Tuple) and len(v.elts) == 3
                   for v in node.values):
            continue
        return {v.elts[0].value for v in node.values
                if isinstance(v.elts[0], ast.Constant)}
    raise AssertionError(
        f"no (fig, canvas, name) roster in {_PANEL_FUNCTION} — its shape has "
        "changed and this guard is measuring nothing")


def _figures_the_dispatch_names() -> set[str]:
    """Figures ``_redraw_bound_figure`` can reach, from both halves of it.

    The inline ``getattr(app, "_x_fig", None) is self._fig`` branches, and the
    module-level roster of features it resolves through the feature registry.
    """
    found = set()
    for node in ast.walk(_func(_SIDEBAR_MODULE, _DISPATCH_FUNCTION)):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            found.add(node.args[1].value)
    for node in ast.walk(_tree(_SIDEBAR_MODULE)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == _FEATURE_ROSTER for t in node.targets):
            continue
        for pair in getattr(node.value, "elts", ()):
            if (isinstance(pair, ast.Tuple) and pair.elts
                    and isinstance(pair.elts[0], ast.Constant)):
                found.add(pair.elts[0].value)
    return {name for name in found if name.endswith("_fig")}


def test_the_dispatch_reaches_every_figure_the_panel_opens_on():
    opens_on = _figures_the_panel_opens_on()
    assert len(opens_on) >= 6, (
        "the Properties roster came back nearly empty — this guard would pass "
        "by measuring nothing")
    missing = sorted(opens_on - _figures_the_dispatch_names())
    assert not missing, (
        "the Properties panel opens on a figure that "
        f"{_DISPATCH_FUNCTION} cannot reach, so every control that needs a "
        "real redraw silently repaints the Line tab instead and reports "
        f"success: {missing}. Add it to {_FEATURE_ROSTER}.")


def test_the_dispatch_names_no_figure_the_panel_never_opens():
    """The other direction: a row for a figure with no Properties panel.

    Not an error the user can see, but it is a claim about the app that has
    stopped being true, and the next reader takes it for a live one.
    """
    stale = sorted(_figures_the_dispatch_names() - _figures_the_panel_opens_on())
    assert not stale, (
        f"{_DISPATCH_FUNCTION} names a figure the Properties panel never opens "
        f"on: {stale}")


@pytest.mark.parametrize("feature", ["Spatial", "Multivariate", "Response Curves"])
def test_each_named_feature_really_owns_a_redraw(feature):
    """The roster's other half is a *feature name*, resolved through the
    registry at runtime. A name that no descriptor carries, or a descriptor
    with no redraw, makes ``features.redraw`` return ``False`` — the sidebar
    then reports the failure it should never have had to."""
    from review.features.registry import build_feature_registry

    registry = build_feature_registry()
    descriptor = registry.get(feature)
    assert descriptor is not None, (
        f"{_FEATURE_ROSTER} names {feature!r}, which the feature registry does "
        "not")
    assert descriptor.redraw is not None, (
        f"{feature!r} has no redraw hook, so the Properties panel cannot "
        "repaint its figure")
    assert callable(descriptor.redraw()), (
        f"{feature!r}'s redraw is not the lazy factory the registry calls")
