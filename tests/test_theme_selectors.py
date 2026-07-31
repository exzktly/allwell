"""Guardrail: no widget is given a style hook that nothing styles.

Two hooks carry the app's styling vocabulary:

* ``setObjectName("X")`` — matched by a ``#X`` selector.
* ``setProperty("variant", "y")`` — matched by a ``[variant="y"]`` selector.

Both are silent when they miss. A label named ``Muted`` whose selector does not
exist renders at full primary emphasis and looks deliberate; a button given
``variant="primary"`` with no rule renders as a plain default button. Before this
test existed, ``Muted`` had 89 call sites and no rule, and only one of eight
``variant`` values resolved — so the app's secondary text tier and its primary
buttons did not exist on screen at all.

A name is considered styled if it appears either in ``theme.qss()`` (app-wide) or
in a stylesheet string inside the same module (widgets that style themselves).
The remainder are listed in ``UNSTYLED_NAMES`` below: those are structural
markers used for lookup and layout rather than appearance. The list only ever
shrinks — adding to it means adding a hook that does nothing, which is the bug
this test exists to prevent.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOTS = [
    REPO_ROOT / "well_viewer",
    REPO_ROOT / "widgets",
    REPO_ROOT / "allwell.py",
]

#: Object names that are deliberately style-free. Each is a handle the code
#: looks up or reasons about, not a request for appearance. Do not add to this
#: list to silence a failure — either write the rule or drop the name.
UNSTYLED_NAMES = frozenset({
    # Data-gating marker: ui_helpers._DATA_GATED_OBJECT_NAMES walks the widget
    # tree for this to disable controls while no dataset is loaded. Giving it a
    # rule would turn behaviour-neutral markup into styling.
    "TabCtrl",
    # Structural handles used by findChild / split view / view-state restore.
    "CentralHost", "CentreSplit", "CentreTabs", "PlotEmptyStateStack",
    "PlotGroupsDrawer", "PlotToolbar", "Sidebar", "VersionPill",
    # Widget-internal handles; these widgets paint themselves in code rather
    # than through a stylesheet string the scan can see.
    "CheckableDropdown", "CheckableDropdownButton", "HueStrip", "SvSquare",
    "KbdHintWrap", "PlateProgress", "PlotCardThemeToggle", "RailNavRowGlyph",
    "RowEditor", "RunDashboard", "SavedSelectionsList",
    # Per-feature handles read back by their controllers.
    "MvFeatureList", "StatsGroupCard",
})


def _py_files():
    for root in ROOTS:
        if root.is_file():
            yield root
        else:
            yield from sorted(root.rglob("*.py"))


def _string_literals(tree):
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _hooks(tree):
    """Yield ``(selector, name)`` for every literal style hook in *tree*."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        args = node.args
        if (node.func.attr == "setObjectName" and args
                and isinstance(args[0], ast.Constant)
                and isinstance(args[0].value, str)):
            yield f"#{args[0].value}", args[0].value
        if (node.func.attr == "setProperty" and len(args) == 2
                and isinstance(args[0], ast.Constant) and args[0].value == "variant"
                and isinstance(args[1], ast.Constant)
                and isinstance(args[1].value, str)):
            yield f'variant="{args[1].value}"', args[1].value


def _offenders():
    theme = pytest.importorskip("theme")
    qss = theme.qss()
    out = []
    for path in _py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        local = _string_literals(tree)
        for selector, name in sorted(set(_hooks(tree))):
            if name in UNSTYLED_NAMES:
                continue
            if selector in qss:
                continue
            if any(selector in text for text in local):
                continue  # styled by a stylesheet in its own module
            out.append(f"{path.relative_to(REPO_ROOT)}: {selector}")
    return out


def test_every_style_hook_resolves():
    offenders = _offenders()
    assert not offenders, (
        "These widgets are given a style hook that no stylesheet matches, so the "
        "hook does nothing:\n  " + "\n  ".join(offenders)
        + "\n\nWrite the rule in theme.qss(), style it locally in the same module, "
          "or drop the name. Add to UNSTYLED_NAMES only for handles that are "
          "deliberately style-free (lookup markers), and say why."
    )


def test_unstyled_allowlist_only_shrinks():
    """Every allowlisted name must still be in use — no stale entries."""
    in_use = set()
    for path in _py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        in_use.update(name for _selector, name in _hooks(tree))
    stale = sorted(UNSTYLED_NAMES - in_use)
    assert not stale, (
        f"UNSTYLED_NAMES lists names nothing sets any more: {stale}. "
        "Remove them — the list is a ratchet and only shrinks."
    )
