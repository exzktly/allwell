"""No function loads a name that is never bound anywhere it can see.

Ruff does not flag this (CLAUDE.md §7.5 #11), and the shape it misses is the
one a rename or a regex sweep leaves behind: replace ``a, b, c = thing()`` with
``obj = thing()`` and every downstream ``a`` becomes a ``NameError`` that no
linter mentions. It shipped twice on one branch —
``barplot/renderer._set_frac_ylim`` raising on every bar redraw, and
``base_panel._render_group_figure`` raising on every batch line export — and
both times a fast lane of several thousand tests stayed green, because the
paths that reach them are `slow`-marked or need a built window.

``symtable`` answers exactly the right question, and answers it the way Python
will: a name is a problem when the compiler resolved it to *global* scope and
the module has no such global. That handles closures, comprehensions, ``except
… as``, ``global``/``nonlocal`` and class bodies without this file knowing
anything about them — a hand-rolled AST walk gets closures wrong and reports
every captured variable in the codebase.

What it deliberately does not catch: a name bound on only one branch and read
after it (``if matched: total = …`` then reading ``total``). That needs flow
analysis. One shipped alongside these, so the shape is real — the regression is
pinned in ``tests/test_fold_change_review_regressions.py`` instead, per member.
"""
from __future__ import annotations

import builtins
import symtable

from tests._repo_scan import python_sources, rel

#: Names the runtime injects into a module without any binding appearing in it.
#: ``__file__`` and friends are the whole list; anything else here would be a
#: hole in the guard rather than an exemption.
_RUNTIME_GLOBALS = frozenset({
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__debug__",
})


def _module_globals(top: symtable.SymbolTable) -> set:
    return {s.get_name() for s in top.get_symbols()} | _RUNTIME_GLOBALS


def _walk(table, path: str, module_names: set, out: list) -> None:
    for sym in table.get_symbols():
        name = sym.get_name()
        if not sym.is_global() or sym.is_assigned():
            continue
        if name in module_names or hasattr(builtins, name):
            continue
        out.append(f"{path}:{table.get_lineno()} {name} (in {table.get_name()})")
    for child in table.get_children():
        _walk(child, path, module_names, out)


def offenders_in(source: str, path: str) -> list[str]:
    """Names *source* loads from module scope that *source* never binds."""
    top = symtable.symtable(source, path, "exec")
    out: list[str] = []
    _walk(top, path, _module_globals(top), out)
    return out


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        try:
            out.extend(offenders_in(path.read_text(), rel(path)))
        except SyntaxError:  # pragma: no cover - a file we cannot parse
            continue
    return out


def test_no_function_reads_a_name_nothing_binds():
    offenders = _offenders()
    assert not offenders, (
        "these names are loaded and never bound — ruff does not flag it, and "
        "the code path that raises may only be reachable from the slow lane:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
