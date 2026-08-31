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
    """String keys of every dict literal assigned inside *func*.

    Every dict rather than one named ``mapping``: pinning the variable name
    would make a rename silently empty the roster, and an empty roster makes
    this guard pass for every key at once — the shape §7.5 #1 warns about,
    where hardening a guard leaves it measuring less than before.
    """
    keys: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


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
