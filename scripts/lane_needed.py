"""Decide whether a change needs a gated test lane.

``tests/_lanes.py::GATES`` names each gate, the rosters it runs, and the few
paths that trigger it by name: ``nanowell`` (the ``--nanowell`` and
``--nanowell-features`` lanes) and ``remote`` (``--remote``). This script answers
one question per gate: can anything this change touched reach one of the
gate's tests?

**Python is followed through the code, not matched by its path.** For every
changed ``.py`` file — application module, test helper or test file alike —
this compares its syntax tree where the branch left the base with the tree now,
and names the definitions that changed: top-level functions, classes,
assignments and imports (an import by the name it binds), and each class's own
members (``Colors.run_done``), with docstrings and comments ignored. Inside the
module the set widens to every definition that refers to a changed one. It then
follows the change outwards through every first-party module that imports the
changed one: in each importer, the definitions that refer to an affected name
are affected in turn, and so on until nothing new is reached. A string constant
naming a first-party module (``importlib.import_module("review.features.x")``)
counts as an import of it by the definition that holds the string.

The lane runs when the change reaches one of its roster files — a roster file
that imports an affected module and refers to an affected name (in its code or
as a fixture parameter), reads the module wholesale (``vars``, ``dir``,
``inspect.getmembers``, ``getattr`` with a computed name), or imports it only
for its side effects — or when it reaches one of the gate's **entry points**
(``tests/_lanes.py::GATE_ENTRY_POINTS``): files the gate's tests run as a
whole rather than by name, such as ``process_microscopy.py``, which the
stand-in cluster executes in a subprocess, and ``tests/conftest.py``, whose
fixtures every test uses without importing them. Any affected name there runs
the lane.

What cannot be narrowed is treated as touching every name the module defines:
a new, deleted or unparseable file, or a change to a module-level statement
that defines nothing (a conditional block, a call). An importer that uses such
a module only for the side effects of importing it becomes undecidable too.

**Paths are for what is not Python.** ``GATES``' patterns name the inputs a
gate's tests read without importing them — the remote requirements files the
bootstrap tests parse — and the harness that runs the lanes: ``scripts/test.sh``,
``tests/requirements-dev.txt``, ``.github/workflows/ci.yml``. Two harness files
are compared rather than matched: ``tests/_lanes.py`` triggers a gate only when
that gate's rosters, patterns or entry points differ, and ``pyproject.toml``
only when its ``[tool.pytest]`` table does. This script and
``scripts/change_scope.py`` decide which lanes run but are run by none of them,
so they trigger no gate; their own tests are in the fast lane.

What this cannot see: code reached by a computed module name, files an
application module reads at run time (fonts, icons, profiles), and anything a
test reaches by a path it builds rather than names. CI runs every gated lane on
each push to ``main``, which is where such a miss is caught.

    python scripts/lane_needed.py <gate> [--base REF]   # exit 0: run it; 1: skip
    python scripts/lane_needed.py <gate> --surface      # print the modules the roster imports directly

The comparison is ``REF...HEAD`` (default ``origin/main``) plus, locally, the
uncommitted and untracked files. When it cannot be made at all — an unknown
ref, no git — the answer is "run it": an unanswerable question must not quietly
skip a lane. Read by ``scripts/preflight.sh`` and by CI.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import _lanes  # noqa: E402

GATES = dict(_lanes.GATES)

#: The fast lane as a gate of its own: every test file in no gated roster. It is not in ``tests/_lanes.py`` because
#: it is not a roster anyone writes — it is what the rosters leave — and it is computed from the tree on each run.
FAST = "fast"


def _fast_roster(root: Path) -> dict[str, str]:
    gated = {name for gate in GATES.values() if gate is not GATES.get(FAST) for roster in gate[0] for name in roster}
    return {p.name: "" for p in sorted((root / "tests").glob("test_*.py")) if p.name not in gated}


GATES[FAST] = ((_fast_roster(REPO),), next(iter(_lanes.GATES.values()))[1])

# ── resolving first-party imports ────────────────────────────────────────────


def _module_file(name: str, root: Path = REPO) -> str | None:
    """The *root*-relative file a dotted first-party module name resolves to. A name that does not resolve from
    the root is tried under ``tests/`` too, where pytest puts the test helpers on ``sys.path``."""
    parts = name.split(".")
    for base_dir in (root, root / "tests"):
        for i in range(len(parts), 0, -1):
            base = base_dir.joinpath(*parts[:i])
            if base.with_suffix(".py").is_file():
                return base.with_suffix(".py").relative_to(root).as_posix()
            if (base / "__init__.py").is_file():
                return (base / "__init__.py").relative_to(root).as_posix()
    return None


def _imported_names(tree: ast.Module) -> set[str]:
    """Every absolute module name *tree* imports, at any nesting, plus ``from X import y`` as ``X.y``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _relative_names(tree: ast.Module, path: str) -> set[str]:
    """The dotted names *tree*'s relative imports (``from . import x``, ``from ..pkg import y``) refer to, resolved
    against the package that holds *path*."""
    names: set[str] = set()
    package = list(Path(path).parent.parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
            prefix = ".".join(base + ([node.module] if node.module else []))
            if prefix:
                names.add(prefix)
                names.update(f"{prefix}.{alias.name}" for alias in node.names)
    return names


def roster_files(gate: str) -> set[str]:
    """The gate's roster files as repository paths."""
    return {f"tests/{name}" for roster in GATES[gate][0] for name in roster}


_READS = {"read_text", "read_bytes", "open"}


def _path_strings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Every string constant in *tree*, plus each ``ROOT / "dir" / "file.py"`` chain joined into ``dir/file.py`` —
    split into those used only as something read (``X.read_text()``, ``X.open()``, ``open(X)``) and the rest."""
    read_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            target = func.value if isinstance(func, ast.Attribute) and func.attr in _READS else \
                node.args[0] if isinstance(func, ast.Name) and func.id == "open" and node.args else None
            if target is not None:
                read_nodes.update(id(sub) for sub in ast.walk(target))
    read: set[str] = set()
    other: set[str] = set()
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 200:
            text = node.value
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts: list[str] = []
            walk = node
            while isinstance(walk, ast.BinOp) and isinstance(walk.op, ast.Div):
                right = walk.right
                if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
                    break
                parts.append(right.value)
                walk = walk.left
            if len(parts) > 1:
                text = "/".join(reversed(parts))
        if text is not None:
            (read if id(node) in read_nodes else other).add(text)
    return read, other


_PARSED: dict[tuple[str, str], tuple[str, ast.Module] | None] = {}

#: A string that names a file ends in an extension: a file whose text has no such string names nothing.
_MAY_NAME_A_FILE = re.compile(r"""\.(py|txt|toml|json|spec|sh|ps1|md|csv|ini|cfg|ya?ml)["']""")


def _parsed(root: Path, rel: str) -> tuple[str, ast.Module] | None:
    """*rel*'s text and syntax tree, read and parsed once per run; ``None`` when it cannot be."""
    key = (str(root), rel)
    if key not in _PARSED:
        try:
            text = (root / rel).read_text(encoding="utf-8")
            _PARSED[key] = (text, ast.parse(text))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            _PARSED[key] = None
    return _PARSED[key]


def namers(gate: str, tracked: list[str], root: Path = REPO) -> dict[str, dict[str, bool]]:
    """The tracked files *gate*'s roster files name by path, each mapped to every roster file that names it and
    whether that file does more than read it. A lane test that names ``process_microscopy.py`` runs it; one that
    names ``all_well_launcher.py`` only reads its source. A change to either file runs the tests that name it; a
    change that merely *reaches* the file through its imports runs them only for a file that is run, since text
    that was read did not change. A string names a file when it is the file's repository path, ends with ``/``
    and that path, or is the bare name of a file at the repository root."""
    by_path = set(tracked)
    rosters = roster_files(gate)
    out: dict[str, dict[str, bool]] = {}
    for rel in sorted(rosters):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _MAY_NAME_A_FILE.search(text):
            continue                                        # no string in it can name a file: no need to parse it
        parsed = _parsed(root, rel)
        if parsed is None:
            continue
        read, other = _path_strings(parsed[1])
        for runs, strings in ((False, read), (True, other)):
            for text in strings:
                text = text.strip().removeprefix("./")
                if not text or text == rel:
                    continue
                if text in by_path:
                    hit = text
                elif "/" in text and "." in text.rsplit("/", 1)[-1]:
                    hits = [p for p in tracked if p.endswith("/" + text)]
                    hit = hits[0] if len(hits) == 1 else None
                else:
                    hit = None
                if hit is None or hit in rosters:
                    continue
                by = out.setdefault(hit, {})
                by[rel] = by.get(rel, False) or runs
    return out


def named_files(gate: str, tracked: list[str], root: Path = REPO) -> dict[str, tuple[str, bool]]:
    """:func:`namers` folded to one roster file per named file (the first by name) and whether any of them runs
    it."""
    return {path: (sorted(by)[0], any(by.values())) for path, by in namers(gate, tracked, root).items()}


def roster_imports(gate: str, root: Path = REPO) -> set[str]:
    """First-party files the gate's roster files import directly, test helpers included."""
    files: set[str] = set()
    for rel in roster_files(gate):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for mod in _imported_names(tree) | _relative_names(tree, rel):
            target = _module_file(mod, root)
            if target is not None and target != rel:
                files.add(target)
    return files


# ── what changed inside a module ─────────────────────────────────────────────
_WHOLE = "<module>"                       # the key every non-definition module statement is filed under
_MAIN = "<main>"                          # the key an `if __name__ == "__main__":` block is filed under
_DYNAMIC_CALLS = {"vars", "dir", "getmembers"}


def _without_docstrings(body: list) -> list:
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _strip(node: ast.AST) -> ast.AST:
    """*node* with the docstring of it and of every def and class inside it removed (in place)."""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            sub.body = _without_docstrings(sub.body) or [ast.Pass()]
    return node


def _defined_names(stmt: ast.stmt) -> list[str]:
    """The names a statement binds, when binding them is all it does."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, ast.Assign):
        return [t.id for t in stmt.targets if isinstance(t, ast.Name)] if all(
            isinstance(t, ast.Name) for t in stmt.targets) else []
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) and isinstance(stmt.target, ast.Name):
        return [stmt.target.id]
    if isinstance(stmt, ast.Import):
        return [alias.asname or alias.name.split(".")[0] for alias in stmt.names]
    if isinstance(stmt, ast.ImportFrom) and not any(alias.name == "*" for alias in stmt.names):
        return [alias.asname or alias.name for alias in stmt.names]
    return []


def _is_main_guard(stmt: ast.stmt) -> bool:
    test = getattr(stmt, "test", None)
    return isinstance(stmt, ast.If) and isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) \
        and test.left.id == "__name__" and len(test.comparators) == 1 \
        and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value == "__main__"


def definitions(source: str) -> dict[str, ast.AST] | None:
    """*source*'s top-level definitions by name — an import by each name it binds, a class also by
    ``Class.member`` for each of its own members, the class itself then standing for its header (bases,
    decorators, keywords) — and every other module statement under :data:`_WHOLE`. Docstrings are dropped,
    comments never reach the tree. ``None`` when it does not parse."""
    try:
        tree = _strip(ast.parse(source))
    except SyntaxError:
        return None
    return _definitions_of(tree)


def _definitions_of(tree: ast.Module) -> dict[str, ast.AST]:
    """:func:`definitions` of an already parsed module, docstrings left as they are."""
    out: dict[str, ast.AST] = {}
    rest: list[ast.stmt] = []
    for stmt in tree.body:
        if _is_main_guard(stmt):
            # What runs only as a script: no importer sees it, an entry point does.
            out[_MAIN] = stmt
            continue
        names = _defined_names(stmt)
        if not names:
            rest.append(stmt)
            continue
        for name in names:
            if isinstance(stmt, ast.ClassDef):
                members = [m for m in stmt.body if _defined_names(m)]
                header = ast.ClassDef(name=stmt.name, bases=stmt.bases, keywords=stmt.keywords, body=[],
                                      decorator_list=stmt.decorator_list, type_params=[])
                out[name] = header
                for member in members:
                    for member_name in _defined_names(member):
                        out[f"{name}.{member_name}"] = member
                others = [m for m in stmt.body if not _defined_names(m)]
                if others:
                    out[f"{name}.{_WHOLE}"] = ast.Module(body=others, type_ignores=[])
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                # One node per bound name, so `from x import a, b` → `from x import a, c` changes only b and c.
                aliases = [a for a in stmt.names if (a.asname or a.name.split(".")[0] if isinstance(stmt, ast.Import)
                                                     else a.asname or a.name) == name]
                node = ast.Import(names=aliases) if isinstance(stmt, ast.Import) else \
                    ast.ImportFrom(module=stmt.module, names=aliases, level=stmt.level)
                previous = out.get(name)
                out[name] = node if previous is None else ast.Module(body=[previous, node], type_ignores=[])
            else:
                out[name] = stmt
    out[_WHOLE] = ast.Module(body=rest, type_ignores=[])
    return out


def _referenced(node: ast.AST) -> set[str]:
    """Every name and attribute name *node* mentions, the parameters it declares (a test names its fixtures that
    way), and the names it imports."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.arg):
            names.add(sub.arg)
        elif isinstance(sub, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in sub.names)
            names.update(alias.name for alias in sub.names)
        elif isinstance(sub, ast.Import):
            for alias in sub.names:
                names.add(alias.asname or alias.name.split(".")[0])
                names.update(alias.name.split("."))
    return names


def _leaf(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def _widen(names: set[str], defined: dict[str, ast.AST]) -> set[str]:
    """*names* plus every definition in *defined* that refers to one of them, until nothing more is added."""
    names = set(names)
    refs = {key: _referenced(node) for key, node in defined.items() if not key.endswith(_WHOLE)}
    grew = True
    while grew:
        grew = False
        for key, mentioned in refs.items():
            if _leaf(key) not in names and mentioned & names:
                names.add(_leaf(key))
                grew = True
    return names


def _all_names(defined: dict[str, ast.AST] | None) -> set[str]:
    """Every name *defined* defines itself — not the names it imports, which it does not change."""
    return {_leaf(key) for key, node in (defined or {}).items()
            if not key.endswith(_WHOLE) and key != _MAIN and not isinstance(node, (ast.Import, ast.ImportFrom))
            and not (isinstance(node, ast.Module) and all(isinstance(n, (ast.Import, ast.ImportFrom))
                                                          for n in node.body))}


def affected_names(old: str | None, new: str | None) -> set[str] | None:
    """The names whose meaning the change from *old* to *new* source can alter: each changed definition (a class
    member by its own name, an import by the name it binds), widened to every definition that refers to one, until
    nothing more is added. ``None`` when that cannot be decided — a file added, deleted or unparseable on either
    side, or a change to a module statement that defines nothing."""
    if old is None or new is None:
        return None
    before, after = definitions(old), definitions(new)
    if before is None or after is None:
        return None
    dump = ast.dump
    if dump(before[_WHOLE]) != dump(after[_WHOLE]):
        return None
    changed = {key for key in before.keys() | after.keys()
               if key not in before or key not in after or dump(before[key]) != dump(after[key])}
    if any(key.endswith(f".{_WHOLE}") for key in changed):
        return None
    return _widen({_leaf(key) for key in changed}, after)


def _reads_wholesale(tree: ast.AST, scope: set[str]) -> bool:
    """Whether *tree* enumerates or looks up by a computed name anything named in *scope*: ``vars(X)``,
    ``dir(X)``, ``inspect.getmembers(X)``, ``getattr(X, <not a string literal>)``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        target = _referenced(node.args[0])
        if fn in _DYNAMIC_CALLS and target & scope:
            return True
        if fn == "getattr" and target & scope and not (
                len(node.args) > 1 and isinstance(node.args[1], ast.Constant)):
            return True
    return False


# ── the import graph of the tree as it stands ───────────────────────────────


def _dotted(path: str) -> str:
    return path[:-3].replace("/", ".").removesuffix(".__init__")


def _chain(node: ast.AST) -> list[str] | None:
    """``a.b.c`` as ``["a", "b", "c"]`` when *node* is a name followed by attribute lookups, else ``None``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return [node.id, *reversed(parts)]
    return None


class _File:
    """One parsed file: its definitions, and for each definition the local names it mentions (for widening inside
    the file) and the first-party names it reaches through an import, fully qualified (for following a change into
    the file). ``from pipeline import io`` then ``io.read(...)`` is a use of ``pipeline.io.read``."""

    __slots__ = ("defined", "local", "qualified", "strings", "star", "whole_reads")

    def __init__(self, defined: dict[str, ast.AST]):
        self.defined = defined
        self.local: dict[str, set[str]] = {}
        self.qualified: dict[str, set[str]] = {}
        self.strings: dict[str, set[str]] = {}
        self.star: set[str] = set()          # modules it star-imports
        self.whole_reads: set[str] = set()   # keys that enumerate or look up by a computed name


class Tree:
    """Every first-party Python file at *root*, with who imports whom — by statement and by a string constant naming
    a module. Files are read up front and parsed only when a change reaches a module whose name they contain, so a
    change that touches a few modules parses a few hundred files rather than all of them. Resolving a name is a
    dictionary lookup, never a filesystem probe."""

    def __init__(self, root: Path, paths: list[str]):
        self.root = root
        self._files: dict[str, _File] = {}
        self.importers: dict[str, set[str]] = {}                 # module file -> files that import it
        self._complete: set[str] = set()                         # modules whose importers are all indexed
        self.unparsed: set[str] = set()
        self.modules: dict[str, str] = {}
        self.texts: dict[str, str] = {}
        for p in paths:
            dotted = _dotted(p)
            self.modules[dotted] = p
            if p.startswith("tests/"):
                self.modules.setdefault(dotted.removeprefix("tests."), p)
            try:
                self.texts[p] = (root / p).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self.unparsed.add(p)

    def file(self, path: str) -> _File | None:
        """*path* parsed and indexed, or ``None`` when it cannot be read or parsed."""
        info = self._files.get(path)
        if info is None and path in self.texts and path not in self.unparsed:
            parsed = _parsed(self.root, path)
            if parsed is None:
                self.unparsed.add(path)
                return None
            self._index(path, parsed[1])
            info = self._files[path]
        return info

    def importers_of(self, path: str) -> set[str]:
        """Every file that imports *path* or names it in a string. Only files whose text holds the module's own name
        can, so those are the only ones parsed."""
        if path not in self._complete:
            stem = Path(path).stem if Path(path).name != "__init__.py" else Path(path).parent.name
            for other, text in self.texts.items():
                if other != path and stem in text:
                    self.file(other)
            self._complete.add(path)
        return self.importers.get(path, set())

    # -- building -----------------------------------------------------------------------------------------------
    def resolve(self, name: str) -> str | None:
        """The file a dotted name lives in: the longest prefix of it that is a module."""
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            hit = self.modules.get(".".join(parts[:i]))
            if hit is not None:
                return hit
        return None

    def canonical(self, name: str) -> str | None:
        """*name* re-rooted at the module file it resolves to (``_fake_remote.X`` -> ``tests._fake_remote.X``)."""
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            hit = self.modules.get(".".join(parts[:i]))
            if hit is not None:
                return ".".join([_dotted(hit), *parts[i:]])
        return None

    def _index(self, path: str, tree: ast.Module) -> None:
        package = list(Path(path).parent.parts)
        bindings: dict[str, str] = {}
        star: set[str] = set()
        info = _File(_definitions_of(tree))
        chains: dict[str, list[list[str]]] = {}
        for key, node in info.defined.items():
            local: set[str] = set()
            strings: set[str] = set()
            raw: list[list[str]] = []
            for sub in ast.walk(node):
                kind = type(sub)
                if kind is ast.Name:
                    local.add(sub.id)
                    raw.append([sub.id])
                elif kind is ast.Attribute:
                    local.add(sub.attr)
                    chain = _chain(sub)
                    if chain:
                        raw.append(chain)
                elif kind is ast.arg:
                    local.add(sub.arg)
                elif kind is ast.Constant:
                    if isinstance(sub.value, str) and len(sub.value) < 120:
                        strings.add(sub.value)
                        target = self.modules.get(sub.value)
                        if target is not None and target != path:
                            self.importers.setdefault(target, set()).add(path)
                elif kind is ast.Call:
                    if sub.args:
                        fn = sub.func.id if isinstance(sub.func, ast.Name) else getattr(sub.func, "attr", "")
                        if fn in _DYNAMIC_CALLS or (fn == "getattr" and not (
                                len(sub.args) > 1 and isinstance(sub.args[1], ast.Constant))):
                            info.whole_reads.add(key)
                elif kind is ast.Import or kind is ast.ImportFrom:
                    self._note_import(sub, path, package, local, bindings, raw, star)
            info.local[key] = local
            info.strings[key] = strings
            chains[key] = raw
        for key, raw in chains.items():
            qualified: set[str] = set()
            for chain in raw:
                if chain[0] == "":
                    dotted = ".".join(chain[1:])
                elif chain[0] in bindings:
                    dotted = ".".join([bindings[chain[0]], *chain[1:]])
                else:
                    continue
                full = self.canonical(dotted)
                if full:
                    qualified.update(_prefixes(full))
            info.qualified[key] = qualified
        info.star = {c for m in star if (c := self.canonical(m))}
        self._files[path] = info

    def _note_import(self, node: ast.Import | ast.ImportFrom, path: str, package: list[str], local: set[str],
                     bindings: dict[str, str], raw: list[list[str]], star: set[str]) -> None:
        """Record what an import statement in *path* binds, the qualified names it takes, and the edge it adds."""
        if isinstance(node, ast.Import):
            for alias in node.names:
                local.add(alias.asname or alias.name.split(".")[0])
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    bindings.setdefault(alias.name.split(".")[0], alias.name.split(".")[0])
                raw.append(alias.name.split("."))
                self._edge(self.resolve(alias.name), path)
            return
        if node.level:
            base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
            module = ".".join(base + ([node.module] if node.module else []))
        else:
            module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                star.add(module)
                continue
            local.add(alias.asname or alias.name)
            full = f"{module}.{alias.name}" if module else alias.name
            bindings[alias.asname or alias.name] = full
            raw.append(["", *full.split(".")])      # already qualified
        for target in {self.resolve(module), *(self.resolve(f"{module}.{a.name}") for a in node.names)}:
            self._edge(target, path)

    def _edge(self, target: str | None, path: str) -> None:
        if target and target != path:
            self.importers.setdefault(target, set()).add(path)

    # -- following a change -------------------------------------------------------------------------------------
    def uses(self, importer: str, module: str, names: set[str] | None) -> tuple[set[str], set[str]] | None:
        """Which of *importer*'s definitions take one of *names* from *module*, and which of the names they take.
        ``None`` when *importer* cannot be read, or imports *module* and names nothing in it (it imports it for what
        importing it does) while *module* changed in a way that cannot be narrowed."""
        info = self.file(importer)
        if info is None:
            return None
        source = self.file(module)
        defined = source.defined if source is not None else {}
        target = names if names is not None else _all_names(defined)
        mdot = _dotted(module)
        wanted = _wanted(mdot, defined, target)
        keys: set[str] = set()
        taken: set[str] = set()
        for key, qualified in info.qualified.items():
            got = {wanted[q] for q in qualified & wanted.keys()}
            if mdot in info.star:
                got |= info.local[key] & target
            strings = info.strings[key]
            if mdot in strings or (mdot in qualified and not got):
                # The module itself is handled as an object: a string naming it, or the module passed around.
                by_name = strings & target
                if by_name or names is None or key in info.whole_reads:
                    got |= by_name or ({"<the module>"} if names is None or key in info.whole_reads else set())
            if got:
                keys.add(key)
                taken |= got
        if names is None and not keys:
            return None
        return keys, taken

    def reaction(self, importer: str, module: str, names: set[str] | None) -> set[str] | None:
        """The names in *importer* that *names* changing in *module* affects (``None``: the whole importer)."""
        found = self.uses(importer, module, names)
        if found is None:
            return None
        keys, _ = found
        if _WHOLE in keys:
            return None
        info = self.file(importer)
        hit = {_leaf(key) for key in keys}
        refs = {k: v for k, v in info.local.items() if not k.endswith(_WHOLE)}
        grew = True
        while grew:
            grew = False
            for key, mentioned in refs.items():
                if _leaf(key) not in hit and mentioned & hit:
                    hit.add(_leaf(key))
                    grew = True
        return hit

    def mentions(self, path: str) -> set[str]:
        info = self.file(path)
        return set().union(set(), *info.local.values(), *info.strings.values()) if info else set()


def _wanted(mdot: str, defined: dict[str, ast.AST], names: set[str]) -> dict[str, str]:
    """The qualified names through which another module can take one of *names* from the module *mdot*, each
    mapped to the name: ``pkg.mod.f`` for a function, ``pkg.mod.Colors.run_done`` for a class attribute — and, for
    a method, the class too, since a method is called on an instance the importer made from the class."""
    out: dict[str, str] = {f"{mdot}.{name}": name for name in names}
    for key, node in defined.items():
        if "." in key and _leaf(key) in names and not key.endswith(_WHOLE):
            out[f"{mdot}.{key}"] = _leaf(key)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(f"{mdot}.{key.split('.', 1)[0]}", key.split(".", 1)[0])
    return out


def _prefixes(dotted: str) -> list[str]:
    parts = dotted.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _source_at(base: str, path: str, root: Path = REPO) -> str | None:
    """*path*'s text where this branch left *base* (their merge base); ``None`` if it did not exist there or git
    cannot say."""
    try:
        merge_base = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=root, capture_output=True, text=True,
                                    encoding="utf-8", check=True).stdout.strip()
        return subprocess.run(["git", "show", f"{merge_base}:{path}"], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _relevant_files(gate: str, paths: list[str], root: Path) -> list[str]:
    """*paths* without the test files no part of *gate* can import. Application code, test helpers, the conftest and
    the gate's roster files stay; another ``test_*.py`` stays only when a kept test file names its module, which is
    how one test borrows another's helpers. Two thirds of the tree is test files, and parsing them is most of the
    cost of a run."""
    tests = {p for p in paths if p.startswith("tests/") and Path(p).name.startswith("test_")}
    kept = [p for p in paths if p not in tests]
    rosters = roster_files(gate)
    frontier = [p for p in tests if p in rosters] + [p for p in kept if p.startswith("tests/")]
    seen = set(frontier)
    by_stem = {Path(p).stem: p for p in tests}
    while frontier:
        path = frontier.pop()
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for stem in re.findall(r"\btest_\w+", text):
            other = by_stem.get(stem)
            if other is not None and other not in seen:
                seen.add(other)
                frontier.append(other)
    return sorted(set(kept) | (seen & tests))


def _python_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return []
    return sorted(p for p in set(out) if (root / p).is_file())


#: What :func:`reach_all` and :func:`select` report when a change cannot be narrowed to particular test files: the
#: whole lane runs.
ALL = "*"


def reach_all(gate: str, changes: dict[str, set[str] | None], tree: Tree,
              named: dict[str, dict[str, bool]] | None = None) -> dict[str, tuple[str, str]]:
    """Every roster file of *gate* that the Python *changes* (path -> affected names, ``None`` for undecidable)
    reach, each with the changed path it came from and the way it took; the key :data:`ALL` when the change reaches
    something every test receives (a conftest hook or autouse fixture, a conftest change that cannot be narrowed).
    *named* is :func:`namers`: a file a roster file runs is reached by any change that reaches the file."""
    rosters = roster_files(gate)
    runners = {path: sorted(r for r, runs in by.items() if runs) for path, by in (named or {}).items()}
    affected: dict[str, set[str] | None] = {}
    came_from: dict[str, str] = {}
    todo: list[str] = []
    out: dict[str, tuple[str, str]] = {}

    def _merge(path: str, names: set[str] | None, source: str | None) -> None:
        if path in affected:
            old = affected[path]
            if old is None or (names is not None and names <= old):
                return
            names = None if names is None else old | names
        affected[path] = names
        if source is not None:
            came_from.setdefault(path, source)
        todo.append(path)

    for path, names in changes.items():
        if names and tree.file(path) is not None:
            names = _widen(names, tree.file(path).defined)
        if names is None or names:
            _merge(path, names, None)

    def _hit(test: str, path: str, why: str) -> None:
        steps = [path]
        while steps[-1] in came_from and steps[-1] not in changes and len(steps) < 12:
            steps.append(came_from[steps[-1]])
        route = " → ".join(reversed(steps))
        out.setdefault(test, (steps[-1], f"{why} (via {route})" if len(steps) > 1 else why))

    while todo and len(out) < len(rosters):
        path = todo.pop(0)
        names = affected[path]
        for test in runners.get(path, []):
            _hit(test, path, f"reaches {path}, which the lane test {test} runs")
        if path == CONFTEST:
            whole, asked = _conftest_reaches(names, tree, rosters)
            if whole:
                _hit(ALL, path, whole)
                return {ALL: out[ALL]}
            for test, why in asked.items():
                _hit(test, path, why)
            continue
        for importer in sorted(tree.importers_of(path)):
            if importer in rosters:
                found = tree.uses(importer, path, names)
                if found is None or found[1] == {"<the module>"}:
                    _hit(importer, path, f"the lane test {importer} imports it whole")
                elif found[1]:
                    _hit(importer, path, f"the lane test {importer} uses "
                                         f"{', '.join(sorted(found[1] - {'<the module>'})[:3])}")
                continue
            reaction = tree.reaction(importer, path, names)
            if reaction is None or reaction:
                _merge(importer, reaction, path)
    return out


def code_reaches(gate: str, changes: dict[str, set[str] | None], tree: Tree,
                 named: dict[str, dict[str, bool]] | None = None) -> tuple[str, str] | None:
    """One reached roster file's (changed path, reason) from :func:`reach_all`, or ``None`` when none is reached."""
    found = reach_all(gate, changes, tree, named)
    return found[sorted(found)[0]] if found else None


CONFTEST = "tests/conftest.py"


def _conftest_reaches(names: set[str] | None, tree: Tree, rosters: set[str]) -> tuple[str, dict[str, str]]:
    """How a change to *names* in ``tests/conftest.py`` reaches the lane: the whole of it when the change cannot be
    narrowed or touches a hook or an autouse fixture (every test receives those) — the first element names why —
    and otherwise each lane test that asks for a changed fixture by name, as a parameter or as a string
    (``usefixtures``, ``getfixturevalue``)."""
    if names is None:
        return "tests/conftest.py changes in a way that cannot be narrowed", {}
    conftest = tree.file(CONFTEST)
    defined = conftest.defined if conftest is not None else {}
    automatic = {name for name in names if name.startswith("pytest_")}
    for name in names:
        node = defined.get(name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(d, ast.Call) and any(k.arg == "autouse" and isinstance(k.value, ast.Constant)
                                                and k.value.value for k in d.keywords)
                for d in node.decorator_list):
            automatic.add(name)
    if automatic:
        return f"tests/conftest.py changes {', '.join(sorted(automatic)[:3])}, which every test receives", {}
    asked: dict[str, str] = {}
    for roster in sorted(rosters):
        wanted = tree.mentions(roster) & names
        if wanted:
            asked[roster] = f"the lane test {roster} asks for the conftest fixture {', '.join(sorted(wanted)[:3])}"
    return "", asked


# ── what changed in tests/_lanes.py and pyproject.toml ───────────────────────
LANES_FILE = "tests/_lanes.py"
PYPROJECT = "pyproject.toml"
VERSION_FILE = "version.py"


def _gate_definition(source: str | None, gate: str) -> tuple | None:
    """*gate*'s rosters (as sets of file names), trigger patterns and entry points in the ``_lanes`` module
    *source*, or ``None`` when it does not load or does not define the gate."""
    if source is None:
        return None
    namespace: dict = {"__name__": "_lanes_snapshot"}
    try:
        exec(compile(source, LANES_FILE, "exec"), namespace)  # noqa: S102 — the repository's own file
        rosters, patterns = namespace["GATES"][gate]
    except Exception:  # noqa: BLE001 — any failure to load means "cannot say", which runs the lane
        return None
    return tuple(frozenset(r) for r in rosters), tuple(patterns)


def lanes_change_selects(gate: str, old_source: str | None, new_source: str | None) -> dict[str, str]:
    """Which of *gate*'s tests a change to ``tests/_lanes.py`` reaches: :data:`ALL` when the gate's patterns differ
    or either version cannot be loaded, otherwise each file that joined the gate's rosters."""
    old, new = _gate_definition(old_source, gate), _gate_definition(new_source, gate)
    if old is None or new is None:
        return {ALL: "tests/_lanes.py: this lane's definition cannot be compared"}
    if old[1] != new[1]:
        return {ALL: "tests/_lanes.py changes this lane's triggers"}
    joined = set().union(set(), *new[0]) - set().union(set(), *old[0])
    return {f"tests/{name}": "tests/_lanes.py adds it to this lane" for name in sorted(joined)}


def lanes_change_reaches(gate: str, old_source: str | None, new_source: str | None) -> str:
    """Why changing ``tests/_lanes.py`` from *old_source* to *new_source* reaches *gate*, or ``""`` when it
    does not."""
    old, new = _gate_definition(old_source, gate), _gate_definition(new_source, gate)
    if old is None or new is None:
        return "lane surface (tests/_lanes.py: this lane's definition cannot be compared)"
    if old != new:
        return "lane surface (tests/_lanes.py changes this lane's rosters or triggers)"
    return ""


def pytest_config_change(old_source: str | None, new_source: str | None) -> str:
    """Why changing ``pyproject.toml`` reaches every gate — its ``[tool.pytest]`` table differs — or ``""``."""
    import tomllib

    def _table(source: str | None) -> dict | None:
        if source is None:
            return None
        try:
            return tomllib.loads(source).get("tool", {}).get("pytest")
        except tomllib.TOMLDecodeError:
            return None

    old, new = _table(old_source), _table(new_source)
    if old is None or new is None or old != new:
        return "lane surface (pyproject.toml: the pytest configuration changes or cannot be compared)"
    return ""


# ── the decision ─────────────────────────────────────────────────────────────


def changed_paths(base: str, root: Path = REPO) -> list[str] | None:
    """Paths changed on this branch against *base*, plus the working tree; None if git cannot say."""
    try:
        committed = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
        working = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(set(committed) | set(working) | set(untracked))


def _read(root: Path, path: str) -> str | None:
    file = root / path
    try:
        return file.read_text(encoding="utf-8") if file.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def path_reason(gate: str, path: str) -> str:
    """Why *path* reaches *gate* by name — a test file in its rosters or a match for one of its patterns — or
    ``""``. Python files the patterns do not name are left to :func:`code_reaches`."""
    if path in roster_files(gate):
        return "a test file in this lane"
    for pattern in GATES[gate][1]:
        if re.search(pattern, path):
            return f"lane surface ({pattern})"
    return ""


def _tracked_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return []
    return sorted(set(out))


_ROOTISH = re.compile(r"\b(ROOT|REPO|REPO_ROOT|_ROOT|HERE|PROJECT)\b|parents\[|parent\.parent|__file__")
_WALKS = {"rglob", "glob", "walk", "iterdir", "listdir", "scandir"}
_WHOLE_TREE = re.compile(r"\b_repo_scan\b|\bpython_sources\b|\bparsed_sources\b|\broot_modules\b")
_WHOLE_TREE_NAMES = {"python_sources", "parsed_sources", "root_modules"}


def _reads_whole_tree(tree: ast.Module) -> bool:
    """Whether *tree* imports ``tests/_repo_scan`` or uses one of its whole-tree readers — in code, not only in a
    string, as a test that builds synthetic sources mentioning them does."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "_repo_scan":
            return True
        if isinstance(node, ast.ImportFrom) and any(a.name == "_repo_scan" for a in node.names):
            return True
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] == "_repo_scan" for a in node.names):
            return True
        if isinstance(node, ast.Name) and node.id in _WHOLE_TREE_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _WHOLE_TREE_NAMES:
            return True
    return False


_MAY_WALK = re.compile(r"\b(rglob|glob|walk|iterdir|listdir|scandir|import_module)\(")


def always_run(files: set[str], root: Path = REPO) -> dict[str, str]:
    """The fast-lane files that must run on every change, each with why: a test that reads the whole tree (a guard
    built on ``tests/_repo_scan``), one that walks the repository itself (a glob or listing rooted at the
    repository), and one that imports a module by a computed name. No import graph can say what such a test
    reads, so no change can be shown not to reach it."""
    out: dict[str, str] = {}
    for rel in sorted(files):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out[rel] = "cannot be read, so cannot be narrowed"
            continue
        whole = _WHOLE_TREE.search(text)
        if not whole and not _MAY_WALK.search(text):
            continue                                        # nothing to find: no need to parse it
        parsed = _parsed(root, rel)
        if parsed is None:
            out[rel] = "cannot be read, so cannot be narrowed"
            continue
        tree = parsed[1]
        if whole and _reads_whole_tree(tree):
            out[rel] = "reads the whole source tree"
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _WALKS and _ROOTISH.search(ast.get_source_segment(text, node) or ""):
                out[rel] = "walks the repository"
                break
            if name == "import_module" and node.args and not isinstance(node.args[0], ast.Constant):
                out[rel] = "imports a module by a computed name"
                break
    return out


def _fast_lanes_change(old_source: str | None, new_source: str | None) -> dict[str, str]:
    """Which fast-lane tests a change to ``tests/_lanes.py`` reaches: each file that left a gated roster, and so
    runs in the fast lane now; :data:`ALL` when the harness patterns change or a version does not load."""
    def _load(source: str | None) -> tuple[set[str], tuple] | None:
        if source is None:
            return None
        namespace: dict = {"__name__": "_lanes_snapshot"}
        try:
            exec(compile(source, LANES_FILE, "exec"), namespace)  # noqa: S102 — the repository's own file
            gated = {name for rosters, _ in namespace["GATES"].values() for roster in rosters for name in roster}
            return gated, tuple(namespace.get("_HARNESS", ()))
        except Exception:  # noqa: BLE001 — any failure to load means "cannot say", which runs the lane
            return None
    old, new = _load(old_source), _load(new_source)
    if old is None or new is None:
        return {ALL: "tests/_lanes.py cannot be compared"}
    if old[1] != new[1]:
        return {ALL: "tests/_lanes.py changes the harness patterns"}
    return {f"tests/{name}": "tests/_lanes.py moves it into the fast lane" for name in sorted(old[0] - new[0])}


def _docs_readers(root: Path) -> list[str]:
    """The test files that read the documents (``scripts/change_scope.py``'s docs lane)."""
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import change_scope

    return change_scope.docs_lane(root, parsed=lambda rel: _parsed(root, rel))


def select(gate: str, base: str, root: Path = REPO) -> dict[str, str]:
    """The roster files of *gate* this change can reach, each with why; ``{ALL: why}`` when the whole lane must run
    (a harness file, the pytest configuration, the lane's own triggers, a conftest hook or autouse fixture, a diff
    that cannot be computed); empty when nothing is reached."""
    paths = changed_paths(base, root)
    if paths is None:
        return {ALL: f"could not diff against {base}"}
    for path in paths:
        # What runs the whole lane is decided by the paths alone; answer it before parsing anything.
        if any(re.search(pattern, path) for pattern in GATES[gate][1]):
            return {ALL: f"{path} is part of the harness that runs the lane"}
        if path == PYPROJECT:
            why = pytest_config_change(_source_at(base, path, root), _read(root, path))
            if why:
                return {ALL: why}
    out: dict[str, str] = {}
    python: dict[str, set[str] | None] = {}
    if gate == FAST:
        GATES[FAST] = ((_fast_roster(root),), GATES[FAST][1])
        out.update(always_run(roster_files(gate), root))
    rosters = roster_files(gate)
    named = namers(gate, _tracked_files(root), root)
    if gate == FAST:
        if any(re.search(pattern, p) for p in paths for pattern in _lanes.DOCS_ONLY_PATTERNS):
            for test in _docs_readers(root):
                if test in rosters:
                    out.setdefault(test, "reads a document that changed")
    for path in paths:
        if path == LANES_FILE:
            changed = (_fast_lanes_change if gate == FAST else
                       lambda old, new: lanes_change_selects(gate, old, new))(_source_at(base, path, root),
                                                                              _read(root, path))
            for test, why in changed.items():
                out.setdefault(test, why)
        elif path == PYPROJECT:
            why = pytest_config_change(_source_at(base, path, root), _read(root, path))
            if why:
                out[ALL] = why
        elif path in rosters:
            out.setdefault(path, "a test file in this lane that changed")
        elif any(re.search(pattern, path) for pattern in GATES[gate][1]):
            out[ALL] = f"{path} is part of the harness that runs the lane"
        else:
            for test in sorted(named.get(path, {})):
                out.setdefault(test, f"names {path}, which changed")
            if path.endswith(".py") and path not in _HARNESS_DECIDERS:
                names = affected_names(_source_at(base, path, root), _read(root, path))
                if path == VERSION_FILE and names is not None:
                    # The number itself reaches no test; a gated test that read it would read whatever it is.
                    names -= {"__version__"}
                python[path] = names
        if ALL in out:
            return {ALL: out[ALL]}
    if python and len(out) < len(rosters):
        tree = Tree(root, _relevant_files(gate, _python_files(root), root))
        for test, (path, why) in reach_all(gate, python, tree, named).items():
            if test == ALL:
                return {ALL: f"{path}: {why}"}
            out.setdefault(test, f"{path}: {why}")
    return out


def decide(gate: str, base: str, root: Path = REPO) -> list[tuple[str, str]] | None:
    """:func:`select` as (test file, reason) pairs, the whole lane as one ``(ALL, reason)`` pair."""
    return sorted(select(gate, base, root).items())


#: The scripts that decide which lanes run. No gated test runs them, so a change to one cannot change a gated
#: test's result; their own tests (``test_a_gated_lane_runs_on_what_changed.py``, ``test_change_scope.py``) are in
#: the fast lane.
_HARNESS_DECIDERS = {"scripts/lane_needed.py", "scripts/change_scope.py"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gate", choices=sorted(GATES))
    parser.add_argument("--base", default=os.environ.get("ALLWELL_SCOPE_BASE") or "origin/main")
    parser.add_argument("--files", action="store_true",
                        help="print the test files to run, one per line, or ALL for the whole lane")
    parser.add_argument("--surface", action="store_true",
                        help="print the modules the roster imports directly, and exit")
    args = parser.parse_args(argv)

    if args.surface:
        print("\n".join(sorted(roster_imports(args.gate))))
        return 0

    try:
        chosen = select(args.gate, args.base)
    except Exception as exc:  # noqa: BLE001 — exit 1 means "skip": an error must never be read as that
        chosen = {ALL: f"could not decide ({type(exc).__name__}: {exc}), so the whole lane runs"}
    if args.files:
        # The files go to stdout for scripts/test.sh and preflight to read; the account of why goes to stderr.
        _report(args.gate, chosen, sys.stderr)
        print("ALL" if ALL in chosen else "\n".join(sorted(chosen)))
        return 0 if chosen else 1
    return _report(args.gate, chosen, sys.stdout)


def _report(gate: str, chosen: dict[str, str], stream) -> int:
    """Say what *chosen* means for the lane on *stream*; the exit status: 0 to run it, 1 to skip it."""
    def say(line: str) -> None:
        print(line, file=stream)

    label = f"{gate} lane"
    if ALL in chosen:
        say(f"{label}: needed, whole — {chosen[ALL]}")
        return 0
    if chosen:
        total = len(roster_files(gate))
        say(f"{label}: needed — {len(chosen)} of its {total} test files can be reached:")
        for test, why in sorted(chosen.items())[:15]:
            say(f"   {test}  [{why}]")
        if len(chosen) > 15:
            say(f"   … and {len(chosen) - 15} more")
        return 0
    say(f"{label}: not needed — no changed path can reach it")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
