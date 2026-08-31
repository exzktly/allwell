"""Every tab's Properties button must name a panel that exists.

A tab reaches the figure-properties sidebar by handing
``core.ui_helpers.make_properties_button`` a plot key, and
``features/export/style.open_export_style_panel`` looks that key up in a
hardcoded roster. Both halves are string literals at opposite ends of the app,
so they can disagree indefinitely: nothing imports across the seam, there is no
shared symbol to rename, and the failure is a status-bar line rather than an
exception.

The Response tab shipped with its Properties button inert for the whole of its
existence — ``make_properties_button(ctx, band, "response")`` against a roster
that had eight keys and not that one. Clicking it wrote "Unknown export panel:
response" to the status bar and opened nothing. Roughly 4,950 fast-lane tests
and a full slow-lane app construction were green throughout, because building
the button is not pressing it and nothing pressed it.

This is §7.6's lesson in roster form: a control that exists is not a control
that works. It is also §7.5 #10's — a roster is a claim about the rest of the
codebase, and a claim nothing checks drifts the moment a feature is added.

Decided from the syntax tree rather than by importing either module: the app
side pulls Qt and the whole feature graph, and this needs to run in the fast
lane in milliseconds.
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import parsed_sources

#: Where the roster lives, and the function that holds it.
_PANEL_MODULE = "well_viewer/features/export/style.py"
_PANEL_FUNCTION = "open_export_style_panel"
#: The helper a tab calls to build the button.
_BUTTON_HELPER = "make_properties_button"


def _panel_keys() -> set[str]:
    """The keys ``open_export_style_panel``'s ``mapping`` dict literal holds."""
    for path, tree in _trees():
        if path.as_posix().endswith(_PANEL_MODULE):
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef)
                        and node.name == _PANEL_FUNCTION):
                    return _dict_keys_in(node)
    raise AssertionError(
        f"{_PANEL_MODULE}::{_PANEL_FUNCTION} not found — this guard is reading "
        "a module that has moved, so it is measuring nothing")


def _dict_keys_in(func: ast.FunctionDef) -> set[str]:
    """Keys of the roster dict inside *func* — the one mapping to 3-tuples.

    Identified by **shape**, not by the variable's name and not by "every dict
    literal here". Naming the variable would let a rename silently empty the
    roster; accepting every dict lets an unrelated one vouch for a key the
    roster lacks, and this function already contains a second dict literal
    (``getattr(win, "_export_style_sidebars", {}) or {}``). A reviewer
    demonstrated the evasion with a `tooltips = {"response": ...}` beside a
    roster that had no `response` row: the guard passed.

    The roster's values are ``(fig_attr, canvas_attr, default_name)`` triples,
    which nothing else in this function is, so that is what selects it.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.Dict) or not node.keys:
            continue
        if not all(isinstance(v, ast.Tuple) and len(v.elts) == 3
                   for v in node.values):
            continue
        return {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return set()


def _trees():
    return parsed_sources()


def _requested_keys() -> dict[str, str]:
    """``{plot key: the file that asks for it}`` across every source root.

    Only literal arguments are decidable. A call passing a variable is reported
    rather than skipped, because a key this guard cannot read is a key it
    cannot protect and silence there would be indistinguishable from a pass.
    """
    found: dict[str, str] = {}
    unreadable: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name != _BUTTON_HELPER:
                continue
            # (ctx, parent, figure_key) — the third positional, or the keyword.
            arg = None
            if len(node.args) >= 3:
                arg = node.args[2]
            for kw in node.keywords:
                if kw.arg == "figure_key":
                    arg = kw.value
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found[arg.value] = path.as_posix()
            elif arg is not None:
                unreadable.append(f"{path.as_posix()}:{node.lineno}")
    assert not unreadable, (
        "make_properties_button was passed a non-literal figure key at "
        + ", ".join(unreadable)
        + " — this guard can only read literals, so that call site is "
        "unprotected. Pass a literal, or extend this guard to resolve it."
    )
    return found


def test_the_guard_can_see_both_halves():
    """A guard that found neither half would pass silently and measure nothing."""
    assert _panel_keys(), "no roster keys parsed out of the panel module"
    assert _requested_keys(), "no make_properties_button call sites found"


def test_every_properties_button_names_a_panel_that_exists():
    panels = _panel_keys()
    missing = {key: where for key, where in _requested_keys().items()
               if key not in panels}
    assert not missing, (
        "These tabs build a Properties button for a plot key "
        f"{_PANEL_FUNCTION} does not carry, so the button writes "
        "'Unknown export panel: <key>' to the status bar and opens nothing:\n"
        + "\n".join(f"  {key!r} — asked for by {where}"
                    for key, where in sorted(missing.items()))
        + f"\nAdd a row to the mapping in {_PANEL_MODULE}."
    )


@pytest.mark.parametrize("key", ["response", "bar", "line"])
def test_the_roster_carries_the_keys_the_plotting_leaves_use(key):
    """Named outright as well as cross-checked.

    The cross-check above compares two sets that are both read from the tree,
    so deleting a tab's button *and* its roster row keeps it green. These pin
    the specific keys, and ``response`` is here because it is the one that
    shipped broken.
    """
    assert key in _panel_keys()


# ── the opener itself, not only the roster ──────────────────────────────────

def test_opening_the_response_panel_gets_past_the_roster_lookup():
    """Driven through ``open_export_style_panel``, which is what the button calls.

    The roster cross-check above compares two sets read from the syntax tree;
    it cannot see whether the opener *behaves*. This presses the same entry
    point with a window that has no figure yet, so the call stops at the very
    next guard — and the two status lines tell the fixed code from the broken
    code exactly:

        before: "Unknown export panel: response"   (key not in the roster)
        after:  "Plot is not ready yet."           (key found, figure absent)

    Asserting the sentence rather than "no error", because a broken key also
    produces a status line and "something was said" passes both ways.
    """
    from well_viewer.features.export.style import open_export_style_panel

    said = []
    win = type("Win", (), {"_set_status": lambda self, text: said.append(text)})()
    open_export_style_panel(win, "response")

    assert said == ["Plot is not ready yet."], said
    assert not any("Unknown export panel" in s for s in said)


# ── the second roster, which the first fix did not sweep ────────────────────
#
# `style.py` holds two rosters answering the same question a hundred lines
# apart: `open_export_style_panel` maps a plot key to its figure, and
# `_resolve_export_dock` maps that figure's attribute to the pre-allocated
# right-side dock the sidebar mounts into. Adding a row to the first and not
# the second is not a half-fix that half-works — `launch_export_editor` takes
# the `dock is None` fallback and appends the sidebar into the plot card's own
# layout, so the panel opens somewhere no other tab puts it. Nothing failed:
# the status line stopped saying "Unknown export panel" and the misplacement
# is invisible to every test that does not lay the widget out.

_DOCK_FUNCTION = "_resolve_export_dock"


def _dock_pairs() -> dict[str, str]:
    """``{figure attr: dock attr}`` from ``_resolve_export_dock``'s tuple roster."""
    for path, tree in _trees():
        if not path.as_posix().endswith(_PANEL_MODULE):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == _DOCK_FUNCTION):
                continue
            pairs = {}
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Tuple) or len(inner.elts) != 2:
                    continue
                a, b = inner.elts
                if (isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and isinstance(b, ast.Constant)
                        and isinstance(b.value, str)):
                    pairs[a.value] = b.value
            return pairs
    raise AssertionError(
        f"{_PANEL_MODULE}::{_DOCK_FUNCTION} not found — the guard is reading a "
        "function that has moved and is measuring nothing")


def _panel_figure_attrs() -> dict[str, str]:
    """``{plot key: figure attr}`` from the panel roster's triples."""
    for path, tree in _trees():
        if not path.as_posix().endswith(_PANEL_MODULE):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == _PANEL_FUNCTION):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Dict) or not inner.keys:
                    continue
                if not all(isinstance(v, ast.Tuple) and len(v.elts) == 3
                           for v in inner.values):
                    continue
                return {k.value: v.elts[0].value
                        for k, v in zip(inner.keys, inner.values)
                        if isinstance(k, ast.Constant)
                        and isinstance(v.elts[0], ast.Constant)}
    return {}


def test_the_two_rosters_in_style_py_cover_the_same_figures():
    """Every figure the panel can open must have a dock to open into."""
    panel = _panel_figure_attrs()
    docks = _dock_pairs()
    assert panel and docks, "one of the two rosters parsed empty"
    missing = {key: attr for key, attr in panel.items() if attr not in docks}
    assert not missing, (
        "These plot keys resolve to a figure with no entry in "
        f"{_DOCK_FUNCTION}, so the Properties sidebar is appended into the "
        "plot card instead of the right-side dock:\n"
        + "\n".join(f"  {key!r} -> {attr}" for key, attr in sorted(missing.items()))
    )


def test_every_docked_figure_is_registered_by_the_tab_that_owns_it():
    """A dock roster entry naming a widget nobody registers is a dead row.

    The mirror of the test above, and the half that catches the opposite
    mistake: a row added to ``_resolve_export_dock`` while the tab still never
    calls ``make_plot_with_right_dock`` reads as swept and resolves to None.
    """
    registered: set[str] = set()
    for _path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None)
            if name != "register" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                registered.add(first.value)
    unregistered = sorted(d for d in _dock_pairs().values()
                          if d not in registered)
    assert not unregistered, (
        "These dock attributes are named by the roster but no tab registers "
        f"them, so they resolve to None: {unregistered}")
