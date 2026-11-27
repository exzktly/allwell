"""A panel closes one way: the ✕ symbol at its top right, named Close in its tooltip and accessible name (design
language §3.6, the user's decision when the menu-and-organisation pull request had made it the word).

The app had four spellings of "close this panel" — the phone strip's buttons
renaming themselves ``Done ✕`` while their sheet was open and ``Hide
controls`` while it was docked, a bare ✕ in a panel's own header, and ``Close
✕`` at a window's foot. The inspector's header and the Wells sheet's carry the
one ✕ now, and the strip's buttons keep their names and show they are open by
being checked.

Held as text, over every string literal in the application: the retired
spellings may not come back, and the one that survives — a *window's* close in
phone layout, which is a different surface from a panel — is rostered.
"""

from __future__ import annotations

import ast

from tests import _surface_guards as scan

#: Spellings that may not be the text of anything.
RETIRED = ("Done ✕", "Hide controls")

#: ``(file, literal)`` kept, with why.
KEPT: dict[tuple[str, str], str] = {
    ("review/features/nanowell/window_view.py", "Hide controls ▾"):
        "kept: a disclosure toggle inside the crops pop-out (it folds the pop-out's own "
        "rows), not the close of a panel",
}


def spellings(trees) -> list[tuple[str, int, str]]:
    """``(file, line, literal)`` for every string literal that is, or starts
    with, a retired spelling or reads ``Close ✕``."""
    out = []
    for rel, tree in trees:
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in docs:
                continue
            text = node.value
            if text == "Close ✕" or any(text == r or text.startswith(r + " ") for r in RETIRED):
                out.append((rel, node.lineno, text))
    return out


def test_no_retired_close_spelling_is_the_text_of_anything():
    found = [f"{f}:{line} {text!r}" for f, line, text in spellings(scan.app_trees())
             if (f, text) not in KEPT]
    assert not found, (
        "a panel closes by a button reading Close at its top right (the inspector's header, "
        "the Wells sheet's); these spell it another way:\n  " + "\n  ".join(found))


def test_the_roster_describes_only_real_literals():
    live = {(f, text) for f, _line, text in spellings(scan.app_trees())}
    gone = [f"{f} {text!r}" for (f, text) in sorted(KEPT) if (f, text) not in live]
    assert not gone, "these KEPT entries no longer match a literal:\n  " + "\n  ".join(gone)
