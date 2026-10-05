"""Who reaches into a module, and by which name — across every access form.

A facade guard that reads only ``from M import X`` statements reports a name as
unused while tests still depend on it: ``import M as m; m.X``,
``monkeypatch.setattr(m, "X", …)``, ``patch.object(m, "X")`` and the string
target ``"M.X"`` all reach the same module attribute without naming it in an
import. Removing an alias on the strength of such a scan breaks those tests,
and the guard that approved the removal stays green. This module answers the
question for all four forms so the remote-runner, pipeline and plotting facade
guards (``tests/test_remote_runner_facade.py``,
``tests/test_process_microscopy_facade.py``,
``tests/test_plotting_facades_are_frozen.py``) share one definition of "a
consumer".

A module is recognised however the consumer spells it: ``import M``, ``import M
as m``, ``from <parent> import <leaf>``, a relative ``from .leaf import X`` or
``from . import leaf`` resolved against the consumer's own package, and
``importlib.import_module("M")``, whether bound to a name or used inline.

Bindings are collected per file rather than per scope. A file that imports the
module as ``rr`` uses ``rr`` for that module throughout in this codebase, and
over-counting a consumer is the safe direction for a liveness check: it keeps
an alias, never removes a live one.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from tests._repo_scan import REPO_ROOT, parse_source, python_sources

#: The four access forms, as recorded against each consumer. ``ATTRIBUTE`` is
#: a read (``m.X``, ``getattr(m, "X")``); the other three *write* the module's
#: binding and are recorded apart, because a write to a pure re-export reaches
#: nothing that runs (see :data:`PATCH_FORMS`).
FROM_IMPORT = "from-import"
ATTRIBUTE = "attribute"
ATTRIBUTE_STORE = "attribute-store"
NAMED_SETATTR = "named-setattr"
STRING_TARGET = "string-target"

#: The forms that replace a module attribute. Applied to a name the module
#: only re-exports, a patch rebinds the alias and leaves the owner — which is
#: what the code calls — untouched, so the patched test measures nothing.
PATCH_FORMS = frozenset({ATTRIBUTE_STORE, NAMED_SETATTR, STRING_TARGET})

#: Call names that write the attribute their second argument names.
_SETTERS = {"setattr", "delattr", "object"}


def consumer_sources() -> list[Path]:
    """Application sources plus every test file (fixtures included)."""
    tests = sorted(p for p in (REPO_ROOT / "tests").rglob("*.py")
                   if "__pycache__" not in p.parts)
    return python_sources() + tests


def _module_name(path: Path, root: Path) -> str | None:
    """``a.b.c`` for ``<root>/a/b/c.py``, or None outside *root*."""
    try:
        parts = list(Path(path).resolve().relative_to(root.resolve()).with_suffix("").parts)
    except ValueError:
        return None
    if parts and parts[-1] == "__init__":
        # A package's own module: ``from .x`` inside it resolves against the
        # package itself, so keep a trailing slot for level 1 to strip.
        parts[-1] = ""
    return ".".join(parts)


def _from_module(node: ast.ImportFrom, consumer: str | None) -> str | None:
    """The absolute module an ``ImportFrom`` names, resolving ``from .x`` and
    ``from . import`` against the consumer's own package."""
    if not node.level:
        return node.module
    if consumer is None:
        return None
    parts = consumer.split(".")
    if node.level >= len(parts) + 1:
        return None  # climbs past the root
    base = [p for p in parts[:-node.level] if p]
    return ".".join([*base, *([node.module] if node.module else [])]) or None


def _module_aliases(tree: ast.AST, module: str,
                    consumer: str | None = None) -> tuple[set[str], bool]:
    """Local names bound to *module*, and whether it is imported unaliased.

    ``import a.b`` binds ``a`` and is used as ``a.b.X``; that dotted form is
    reported by the second return value and matched as an attribute chain.
    Relative imports are resolved against *consumer*, the importing module.
    """
    parent, _, leaf = module.rpartition(".")
    names: set[str] = set()
    dotted = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    if alias.asname:
                        names.add(alias.asname)
                    else:
                        dotted = True
        elif (isinstance(node, ast.ImportFrom) and parent
              and _from_module(node, consumer) == parent):
            for alias in node.names:
                if alias.name == leaf:
                    names.add(alias.asname or leaf)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)
              and isinstance(node.value, ast.Call)
              and _is_import_module_call(node.value, module)):
            names.add(node.targets[0].id)
    return names, dotted


def _is_import_module_call(call: ast.Call, module: str) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else "")
    return (name == "import_module" and bool(call.args)
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == module)


def _dotted(node: ast.AST) -> str | None:
    """``a.b.c`` for a pure Name/Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _refers_to_module(node: ast.AST, aliases: set[str], dotted: bool,
                      module: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Call):  # importlib.import_module("m").X, inline
        return _is_import_module_call(node, module)
    return dotted and _dotted(node) == module


def consumers_of(module: str, sources: Iterable[Path] | None = None,
                 *, exclude: Iterable[Path] = (),
                 root: Path = REPO_ROOT) -> dict[str, set[tuple[str, str]]]:
    """Map each attribute name reached on *module* to ``{(file, form)}``.

    *exclude* removes files from the scan (normally the module itself). *root*
    is what a consumer's own module name is read against, to resolve its
    relative imports.
    """
    skip = {Path(p).resolve() for p in exclude}
    # A deeper target (``"m.subprocess.run"``) reaches ``subprocess`` through
    # *module* just as ``"m.subprocess"`` does: the patch resolves the first
    # attribute on *module* and then walks on from there.
    pattern = re.compile(
        rf"^{re.escape(module)}\.([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z0-9_.]*)?$")
    users: dict[str, set[tuple[str, str]]] = {}

    def add(name: str, path: Path, form: str) -> None:
        try:
            shown = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            shown = path.as_posix()
        users.setdefault(name, set()).add((shown, form))

    for path in (consumer_sources() if sources is None else sources):
        if Path(path).resolve() in skip:
            continue
        tree = parse_source(path)
        consumer = _module_name(path, root)
        aliases, dotted = _module_aliases(tree, module, consumer)
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and _from_module(node, consumer) == module):
                for alias in node.names:
                    add(alias.name, path, FROM_IMPORT)
            elif isinstance(node, ast.Attribute) and _refers_to_module(
                    node.value, aliases, dotted, module):
                store = isinstance(node.ctx, (ast.Store, ast.Del))
                add(node.attr, path, ATTRIBUTE_STORE if store else ATTRIBUTE)
            elif (isinstance(node, ast.Call) and len(node.args) >= 2
                  and isinstance(node.args[1], ast.Constant)
                  and isinstance(node.args[1].value, str)
                  and _refers_to_module(node.args[0], aliases, dotted, module)):
                func = node.func
                fname = func.attr if isinstance(func, ast.Attribute) else (
                    func.id if isinstance(func, ast.Name) else "")
                add(node.args[1].value, path,
                    NAMED_SETATTR if fname in _SETTERS else ATTRIBUTE)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = pattern.match(node.value)
                if match:
                    add(match.group(1), path, STRING_TARGET)
    return users


def module_level_names(path: Path) -> dict[str, str]:
    """Names bound at module scope -> ``"import"`` or ``"defined"``.

    An import-bound name is a re-export candidate; a defined one (def, class,
    assignment) is owned here.
    """
    out: dict[str, str] = {}
    for node in _module_level_statements(parse_source(path).body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name.split(".")[0]
                out.setdefault(bound, "import")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = "defined"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        out[sub.id] = "defined"
    return out


def _module_level_statements(body):
    """Module-scope statements, including those inside a module-level ``if``,
    ``try`` or ``with`` — a guarded import binds a module name all the same —
    but not inside a function or class."""
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            yield from _module_level_statements(getattr(node, field, []) or [])
        for handler in getattr(node, "handlers", []) or []:
            yield from _module_level_statements(handler.body)


def _string_annotations(tree: ast.AST):
    """String constants in annotation positions."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                        *(a for a in (args.vararg, args.kwarg) if a)):
                if arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def _all_entries(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                and any(isinstance(t, ast.Name) and t.id == "__all__"
                        for t in (node.targets if isinstance(node, ast.Assign)
                                  else [node.target]))):
            out |= {c.value for c in ast.walk(node.value)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return out


def names_loaded_in(path: Path) -> set[str]:
    """Every bare name the module reads in its own body.

    Includes names inside string annotations, which are read when the
    annotation is evaluated, and the module's ``__all__``, which is its
    declared export surface. Other string constants do not count: a log
    message or a dict key that happens to spell a re-exported name does not
    read it.
    """
    tree = parse_source(path)
    loaded = {n.id for n in ast.walk(tree)
              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    for annotation in _string_annotations(tree):
        for node in ast.walk(annotation):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                try:
                    expr = ast.parse(node.value, mode="eval")
                except SyntaxError:
                    continue
                loaded |= {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}
    return loaded | _all_entries(tree)


def reexports(path: Path) -> set[str]:
    """Module-scope imports the module itself never reads: pure re-exports."""
    bound = module_level_names(path)
    own = names_loaded_in(path)
    return {n for n, kind in bound.items()
            if kind == "import" and n not in own and n != "annotations"}
