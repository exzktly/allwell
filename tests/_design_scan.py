"""Shared machinery for the design-language guards (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §5.2).

Each guard counts, per file, the sites that break one rule of the design language, and compares the counts with
a roster of the sites that existed when the guard was written (``tests/_design_rosters.py``). A file may hold no
more sites than its roster entry — a new site fails — and no fewer than its entry without the entry being lowered:
the liveness half fails until the roster is shrunk to match, so a roster only ever shrinks and never goes on
excusing sites that are gone. A file with no entry may hold none.

The guards read the tree through ``tests/_repo_scan`` (CLAUDE.md §2 rule 2), and each is handed a synthetic
violation in ``tests/test_guards_bite.py``.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable, Iterable

from tests._repo_scan import parse_source


def call_name(call: ast.Call) -> str:
    """The called name: ``setText`` for ``label.setText(…)``, ``QLabel`` for ``QLabel(…)``."""
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def strings_in(node: ast.AST) -> list[ast.Constant]:
    """Every string constant in *node*: a literal, either arm of a conditional, each item of a tuple. Not the
    parts of an f-string, whose text is not known until it runs."""
    if isinstance(node, ast.JoinedStr):
        return []
    if isinstance(node, ast.Constant):
        return [node] if isinstance(node.value, str) else []
    return [c for child in ast.iter_child_nodes(node) for c in strings_in(child)]


def count_sites(find: Callable[[str, ast.Module], Iterable[object]], sources: Iterable, rel: Callable, *,
                skip: Callable[[str], bool] = lambda _path: False) -> Counter:
    """``{file: number of sites}`` over *sources*; *find* yields one item per site in a file. The guard passes
    its own ``python_sources()`` and ``rel``, which is what ``tests/test_guards_bite.py`` redirects."""
    seen: Counter = Counter()
    for path in sources:
        r = rel(path)
        if skip(r):
            continue
        n = sum(1 for _ in find(r, parse_source(path)))
        if n:
            seen[r] = n
    return seen


def over_roster(seen: Counter, roster: dict[str, int]) -> list[str]:
    """Files holding more sites than their roster entry allows (none, for a file with no entry)."""
    return sorted(f"{path}: {n} site{'s' * (n > 1)} (roster {roster.get(path, 0)})"
                  for path, n in seen.items() if n > roster.get(path, 0))


def stale_roster(seen: Counter, roster: dict[str, int]) -> list[str]:
    """Roster entries larger than what the file now holds: lower them, so the roster only shrinks."""
    return sorted(f"{path}: roster {n}, found {seen.get(path, 0)}"
                  for path, n in roster.items() if seen.get(path, 0) < n)
