"""Decide whether a change needs a gated test lane.

``tests/_lanes.py::GATES`` names each gate, the rosters it runs, and the path
patterns that trigger it: ``nanowell`` (the ``--nanowell`` and
``--nanowell-features`` lanes) and ``remote`` (``--remote``). This script answers
one question per gate: does any changed path fall in its surface?

The surface is the gate's patterns plus every first-party module one of its
roster files imports directly, read from the test files' own syntax trees each
time this runs. Computing the second half rather than writing it down means a
new test that imports a new module extends the trigger by joining the roster.

**A directly imported module triggers the lane only through what changed in
it.** Importing a module is weak evidence on its own: ``ui/theme.py`` is
imported by nearly every test, so a one-colour edit there used to start every
gated lane on both machines. For a changed module that reaches a lane only by
being imported, this compares its syntax tree on the base with the tree now and
names the top-level definitions that changed — functions, classes, assignments,
and each class's own members (``Colors.run_done``), with docstrings and
comments ignored. It then widens that set to every definition in the module
that refers to a changed one, until nothing more is added. The lane runs when
one of its roster files that imports the module refers to one of those names,
or reads the module wholesale (``vars``, ``dir``, ``getattr`` with a computed
name, ``inspect.getmembers``). Anything this cannot decide runs the lane: a
new, deleted or unparseable file, or a change to a module-level statement that
is not a definition (an import, a conditional block, a call).

**``tests/_lanes.py`` triggers a lane only by changing that lane.** It holds
every roster, so adding any test anywhere — a UI test, a docs pattern — used to
count as a harness change for both gates, and did on a third of merges. Both
versions are loaded and the lane's own definition compared: the files in each
of its rosters and its trigger patterns. When those are equal the lane is not
affected; when either version cannot be loaded, it is.

What this cannot see is a change reaching a test through a module the test did
not import itself, the same blind spot the import rule always had. CI runs
every gated lane on each push to ``main``, which is where that is caught.

    python scripts/lane_needed.py <gate> [--base REF]   # exit 0: run it; 1: skip
    python scripts/lane_needed.py <gate> --surface      # print the computed module set

The comparison is ``REF...HEAD`` (default ``origin/main``) plus, locally, the
uncommitted and untracked files. When it cannot be made at all — an unknown
ref, no git — the answer is "run it": an unanswerable question must not quietly
skip a lane. Read by ``scripts/preflight.sh`` and by CI.
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from _lanes import GATES  # noqa: E402


def _module_file(name: str, root: Path = REPO) -> str | None:
    """The *root*-relative file a dotted first-party module name resolves to."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        base = root.joinpath(*parts[:i])
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py").relative_to(root).as_posix()
        if (base / "__init__.py").is_file():
            return (base / "__init__.py").relative_to(root).as_posix()
    return None


def _imported_names(tree: ast.Module) -> set[str]:
    """Every module name *tree* imports, at any nesting, plus ``from X import y`` as ``X.y``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def roster_imports(gate: str) -> set[str]:
    """First-party files the gate's roster files import directly (tests/ excluded)."""
    files: set[str] = set()
    for roster in GATES[gate][0]:
        for name in roster:
            tree = ast.parse((REPO / "tests" / name).read_text(encoding="utf-8"))
            for mod in _imported_names(tree):
                if mod.split(".")[0] == "tests":
                    continue
                path = _module_file(mod)
                if path is not None:
                    files.add(path)
    return files


def surface_reason(gate: str, path: str, imports: set[str]) -> str:
    """Why *path* triggers *gate*'s lanes, or ``""`` when it does not. A path matched only by the import half
    reads ``"imported directly by a test in this lane"``; :func:`import_reason` then decides whether what changed
    in it reaches those tests."""
    rosters, patterns = GATES[gate]
    if path.startswith("tests/") and any(Path(path).name in r for r in rosters):
        return "a test file in this lane"
    for pattern in patterns:
        if re.search(pattern, path):
            return f"lane surface ({pattern})"
    if path in imports:
        return "imported directly by a test in this lane"
    return ""


# ── what changed in tests/_lanes.py ──────────────────────────────────────────
LANES_FILE = "tests/_lanes.py"


def _gate_definition(source: str | None, gate: str) -> tuple | None:
    """*gate*'s rosters (as sets of file names) and trigger patterns in the ``_lanes`` module *source*, or
    ``None`` when it does not load or does not define the gate."""
    if source is None:
        return None
    namespace: dict = {"__name__": "_lanes_snapshot"}
    try:
        exec(compile(source, LANES_FILE, "exec"), namespace)  # noqa: S102 — the repository's own file
        rosters, patterns = namespace["GATES"][gate]
    except Exception:  # noqa: BLE001 — any failure to load means "cannot say", which runs the lane
        return None
    return tuple(frozenset(r) for r in rosters), tuple(patterns)


def lanes_reason(gate: str, base: str, root: Path = REPO) -> str:
    """Why a change to ``tests/_lanes.py`` reaches *gate*, or ``""`` when the gate's rosters and patterns are
    the same on both sides."""
    new_file = root / LANES_FILE
    return lanes_change_reaches(gate, _source_at(base, LANES_FILE, root),
                                new_file.read_text(encoding="utf-8") if new_file.is_file() else None)


def lanes_change_reaches(gate: str, old_source: str | None, new_source: str | None) -> str:
    """Why changing ``tests/_lanes.py`` from *old_source* to *new_source* reaches *gate*, or ``""`` when it
    does not."""
    old, new = _gate_definition(old_source, gate), _gate_definition(new_source, gate)
    if old is None or new is None:
        return "lane surface (tests/_lanes.py: this lane's definition cannot be compared)"
    if old != new:
        return "lane surface (tests/_lanes.py changes this lane's rosters or triggers)"
    return ""


# ── what changed inside a module ─────────────────────────────────────────────
_WHOLE = "<module>"                       # the key every non-definition module statement is filed under
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
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, ast.Assign):
        return [t.id for t in stmt.targets if isinstance(t, ast.Name)] if all(
            isinstance(t, ast.Name) for t in stmt.targets) else []
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) and isinstance(stmt.target, ast.Name):
        return [stmt.target.id]
    return []


def definitions(source: str) -> dict[str, ast.AST] | None:
    """*source*'s top-level definitions by name — a class also by ``Class.member`` for each of its own members,
    the class itself then standing for its header (bases, decorators, keywords) — and every other module statement
    under :data:`_WHOLE`. Docstrings are dropped, comments never reach the tree. ``None`` when it does not parse."""
    try:
        tree = _strip(ast.parse(source))
    except SyntaxError:
        return None
    out: dict[str, ast.AST] = {}
    rest: list[ast.stmt] = []
    for stmt in tree.body:
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
            else:
                out[name] = stmt
    out[_WHOLE] = ast.Module(body=rest, type_ignores=[])
    return out


def _referenced(node: ast.AST) -> set[str]:
    """Every name and attribute name *node* mentions, and the names it imports."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in sub.names)
            names.update(alias.name for alias in sub.names)
    return names


def _leaf(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def affected_names(old: str | None, new: str | None) -> set[str] | None:
    """The names whose meaning the change from *old* to *new* source can alter: each changed definition (a class
    member by its own name), widened to every definition that refers to one, until nothing more is added. ``None``
    when that cannot be decided — a file added, deleted or unparseable on either side, or a change to a module
    statement that defines nothing."""
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
    names = {_leaf(key) for key in changed}
    refs = {key: _referenced(node) for key, node in after.items() if key != _WHOLE}
    grew = True
    while grew:
        grew = False
        for key, mentioned in refs.items():
            if _leaf(key) not in names and mentioned & names:
                names.add(_leaf(key))
                grew = True
    return names


def _module_aliases(tree: ast.Module, path: str) -> set[str]:
    """The local names under which *tree* binds the module at *path* itself (``import ui.theme as t``,
    ``from ui import theme``), so a wholesale read of it can be recognised."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_file(alias.name) == path:
                    out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if _module_file(f"{node.module}.{alias.name}") == path and \
                        _module_file(node.module) != path:
                    out.add(alias.asname or alias.name)
    return out


def _reads_wholesale(tree: ast.Module, scope: set[str]) -> bool:
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


def import_reason(gate: str, path: str, base: str, root: Path = REPO) -> str:
    """For a *path* that reaches *gate* only by being imported by one of its tests: why what changed in it reaches
    them, or ``""`` when it cannot (:func:`roster_use`)."""
    old = _source_at(base, path, root)
    new_file = root / path
    new = new_file.read_text(encoding="utf-8") if new_file.is_file() else None
    names = affected_names(old, new)
    if names is None:
        return "imported directly by a test in this lane; what changed in it cannot be narrowed"
    return roster_use(gate, path, names, definitions(new) or {}, root)


def roster_use(gate: str, path: str, names: set[str], defined: dict, root: Path = REPO) -> str:
    """Why a roster file of *gate* that imports *path* is reached by a change to *names* in it: it refers to one of
    them, or reads the module or one of its top-level definitions (*defined*) wholesale. ``""`` when none is."""
    if not names:
        return ""
    scope = {k for k in defined if "." not in k and k != _WHOLE}
    for roster in GATES[gate][0]:
        for name in sorted(roster):
            tree = ast.parse((root / "tests" / name).read_text(encoding="utf-8"))
            if path not in {_module_file(m, root) for m in _imported_names(tree)}:
                continue
            hit = sorted(_referenced(tree) & names)
            if hit:
                return f"imported directly by {name}, which uses {', '.join(hit[:3])}"
            if _reads_wholesale(tree, _module_aliases(tree, path) | scope):
                return f"imported directly by {name}, which reads it wholesale"
    return ""


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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gate", choices=sorted(GATES))
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--surface", action="store_true",
                        help="print the modules the roster imports, and exit")
    args = parser.parse_args(argv)

    imports = roster_imports(args.gate)
    if args.surface:
        print("\n".join(sorted(imports)))
        return 0

    label = f"{args.gate} lanes"
    paths = changed_paths(args.base)
    if paths is None:
        print(f"{label}: needed (could not diff against {args.base})")
        return 0
    hits = []
    for p in paths:
        why = surface_reason(args.gate, p, imports)
        if p == LANES_FILE:
            why = lanes_reason(args.gate, args.base)
        elif why.startswith("imported directly"):
            why = import_reason(args.gate, p, args.base)
        if why:
            hits.append((p, why))
    if hits:
        print(f"{label}: needed — {len(hits)} changed path(s) can reach them:")
        for path, why in hits[:15]:
            print(f"   {path}  [{why}]")
        if len(hits) > 15:
            print(f"   … and {len(hits) - 15} more")
        return 0
    print(f"{label}: not needed — none of {len(paths)} changed path(s) can reach them")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
