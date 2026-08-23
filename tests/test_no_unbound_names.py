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

``symtable`` has one blind spot this file covers separately. Every module here
carries ``from __future__ import annotations``, so an annotation is never
compiled as a load — it is stored as a string and only evaluated if something
calls ``typing.get_type_hints``. A name that appears *only* in an annotation is
therefore invisible to the compiler, to ``symtable`` and to ruff alike (F821 is
in this repo's ignore list). Extracting a module dropped an ``Iterable`` import
that way: the annotation still read ``Iterable[Path | str]``, nothing raised at
import or at call, and ``get_type_hints`` on that function raised ``NameError``.
``test_no_annotation_reads_a_name_nothing_binds`` walks the annotations the
compiler skipped and asks the same question of them.

What this file deliberately does not catch: a name bound on only one branch and
read after it (``if matched: total = …`` then reading ``total``). That needs
flow analysis. One shipped alongside these, so the shape is real — the
regression is pinned in ``tests/test_fold_change_review_regressions.py``
instead, per member.
"""
from __future__ import annotations

import ast
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


def _annotation_names(node: ast.AST) -> set[str]:
    """Every name an annotation *loads*, ignoring attribute and subscript keys.

    ``a.b`` contributes ``a``; ``dict[str, int]`` contributes ``dict``, ``str``
    and ``int``; a string forward reference contributes nothing, because it is
    resolved against a namespace this file cannot see.
    """
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            out.add(sub.id)
    return out


def annotation_offenders_in(source: str, path: str) -> list[str]:
    """Names *source* annotations load that *source* never binds.

    Under ``from __future__ import annotations`` these are never compiled, so
    neither ``symtable`` above nor ruff can see them; they surface only when
    something calls ``typing.get_type_hints``.
    """
    tree = ast.parse(source, path)
    bound: set[str] = set(_RUNTIME_GLOBALS)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias):  # pragma: no cover - covered above
            bound.add(node.asname or node.name)

    out: list[str] = []
    for node in ast.walk(tree):
        annotations = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                      args.vararg, args.kwarg):
                if a is not None and a.annotation is not None:
                    annotations.append(a.annotation)
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for ann in annotations:
            for name in sorted(_annotation_names(ann)):
                if name in bound or hasattr(builtins, name):
                    continue
                out.append(f"{path}:{ann.lineno} {name}")
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


def test_no_annotation_reads_a_name_nothing_binds():
    """An annotation-only name is invisible to the compiler and to ruff.

    ``from __future__ import annotations`` is on every module here, so this is
    the one place the missing import shows up before ``get_type_hints`` does.
    """
    offenders: list[str] = []
    for path in python_sources():
        try:
            offenders.extend(
                annotation_offenders_in(path.read_text(), rel(path)))
        except SyntaxError:  # pragma: no cover - a file we cannot parse
            continue
    assert not offenders, (
        "these annotations name something the module never imports or binds, "
        "not even under TYPE_CHECKING. Nothing raises at import or at call, "
        "and ruff cannot see it because F821 is ignored in this repo — but "
        "typing.get_type_hints() on them raises NameError, and the annotation "
        "documents a type the reader cannot follow:\n"
        + "\n".join(f"  {o}" for o in sorted(set(offenders)))
    )
