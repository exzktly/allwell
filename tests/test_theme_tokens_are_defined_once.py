"""A design token is assigned once in its class.

A class body that assigns the same name twice keeps the second silently. Adding
a ``focus_ring`` plot colour beside the existing keyboard ``focus_ring`` token
did exactly that: the plot ring got ``rgba(107, 138, 253, 0.35)``, a QSS colour
matplotlib cannot parse, and the Scatter canvas raised on its next draw. Reads
the source through the shared parse (CLAUDE.md §2 rule 2).
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from tests._repo_scan import parse_source

THEME = Path(__file__).resolve().parent.parent / "ui" / "theme.py"


def duplicate_tokens(tree: ast.Module) -> list[str]:
    found = []
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        names = []
        for stmt in cls.body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else (
                [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
            names += [t.id for t in targets if isinstance(t, ast.Name)]
        found += [f"{cls.name}.{n}" for n, k in Counter(names).items() if k > 1]
    return found


def test_no_token_is_assigned_twice_in_one_class():
    assert duplicate_tokens(parse_source(THEME)) == []


def test_the_check_sees_a_second_assignment():
    tree = ast.parse("class Colors:\n    focus_ring = '#FFE640'\n    focus_ring = 'rgba(1, 2, 3, 0.3)'\n")
    assert duplicate_tokens(tree) == ["Colors.focus_ring"]
