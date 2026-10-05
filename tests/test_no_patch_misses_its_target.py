"""No test patches a name on a module that only re-exports it.

``monkeypatch.setattr(module, "name", fake)`` rebinds ``module.name``. If the
module merely re-exports ``name`` — imports it and never reads it — nothing that
runs looks the name up there: the owner's code calls its own binding, and the
patch changes nothing. The test then passes for whatever reason it would have
passed anyway, and says it proved something.

Found while classifying ``process_microscopy``'s re-exports (row A0 of
the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it)):
``tests/test_model_recovery.py`` patched ``process_microscopy._pid_alive`` to say
a lock holder was dead. The lock code reads ``pipeline.stardist_cache._pid_alive``;
the test passed only because PID 999999 really is dead, and went on passing with
the patch flipped to "alive". Patched on the owner, flipping it makes the test
wait out the lock timeout and fail — which is what a patch that matters does.

A re-export *is* a live target when application code looks the name up on that
module at call time — ``mod.X(...)`` through a module alias, or
``from mod import X`` inside a function body — and such names are not flagged.

Patch at the module whose code reads the name. Every patch form is checked:
``monkeypatch.setattr(m, "X", …)``, ``patch.object(m, "X")``, ``setattr(m, "X",
…)``, ``m.X = …`` and the string target ``"pkg.mod.X"``.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

from tests._facade_scan import reexports
from tests._repo_scan import REPO_ROOT, parse_source, python_sources

_SETTERS = {"setattr", "delattr", "object"}


@cache
def _module_paths() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in python_sources():
        rel = path.relative_to(REPO_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            out[".".join(parts)] = path
    return out


@cache
def _reexports_of(module: str) -> frozenset[str]:
    path = _module_paths().get(module)
    return frozenset() if path is None else frozenset(reexports(path))


@cache
def _read_at_call_time() -> frozenset[tuple[str, str]]:
    """``(module, name)`` pairs application code looks up when it runs.

    A module-scope ``from mod import X`` binds once at import, so a later patch
    of ``mod.X`` does not reach it; an attribute read through a module alias, or
    a ``from mod import X`` inside a function, looks the name up every call.
    """
    mods = _module_paths()
    pairs: set[tuple[str, str]] = set()
    for path in python_sources():
        tree = parse_source(path)
        names, dotted = _bindings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                mod = _module_of(node.value, names, dotted)
                if mod:
                    pairs.add((mod, node.attr))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.ImportFrom) and node.module in mods:
                        for alias in node.names:
                            pairs.add((node.module, alias.name))
    return frozenset(pairs)


def _bindings(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """Local name -> first-party module, and modules imported unaliased."""
    mods = _module_paths()
    names: dict[str, str] = {}
    dotted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in mods:
                    if alias.asname:
                        names[alias.asname] = alias.name
                    else:
                        dotted.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if full in mods:
                    names[alias.asname or alias.name] = full
    return names, dotted


def _module_of(node: ast.AST, names: dict[str, str], dotted: set[str]) -> str | None:
    if isinstance(node, ast.Name):
        return names.get(node.id)
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        text = ".".join([node.id, *reversed(parts)])
        return text if text in dotted else None
    return None


def _patches(tree: ast.AST):
    """Yield ``(module, name, line)`` for every patch of a module attribute."""
    names, dotted = _bindings(tree)
    mods = _module_paths()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            mod = _module_of(node.value, names, dotted)
            if mod:
                yield mod, node.attr, node.lineno
        elif isinstance(node, ast.Call):
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else "")
            if (fname in _SETTERS and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                mod = _module_of(node.args[0], names, dotted)
                if mod:
                    yield mod, node.args[1].value, node.lineno
            elif (fname in ("patch", "setattr") and node.args
                  and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)):
                mod, _, attr = node.args[0].value.rpartition(".")
                if mod in mods:
                    yield mod, attr, node.lineno


def _test_files() -> list[Path]:
    return sorted(p for p in (REPO_ROOT / "tests").rglob("*.py")
                  if "__pycache__" not in p.parts)


def _offenders(files) -> list[str]:
    out = []
    for path in files:
        for module, name, line in _patches(parse_source(path)):
            if (name in _reexports_of(module)
                    and (module, name) not in _read_at_call_time()):
                try:
                    shown = path.relative_to(REPO_ROOT).as_posix()
                except ValueError:
                    shown = str(path)
                out.append(f"{shown}:{line} patches {module}.{name}, "
                           "which that module only re-exports")
    return out


def test_no_test_patches_a_name_its_module_only_re_exports():
    offenders = _offenders(_test_files())
    assert not offenders, (
        "These patches rebind an alias nothing reads; patch the module whose "
        "code calls the name:\n  " + "\n  ".join(offenders))
