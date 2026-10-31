"""Decide whether a change needs a gated test lane.

``tests/_lanes.py::GATES`` names each gate, the rosters it runs, and the path
patterns that trigger it: ``nanowell`` (the ``--nanowell`` and
``--nanowell-features`` lanes) and ``remote`` (``--remote``). This script answers
one question per gate: does any changed path fall in its surface?

The surface is the gate's patterns plus every first-party module one of its
roster files imports directly, read from the test files' own syntax trees each
time this runs. Computing the second half rather than writing it down means a
new test that imports a new module extends the trigger by joining the roster.

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
    """Why *path* triggers *gate*'s lanes, or ``""`` when it does not."""
    rosters, patterns = GATES[gate]
    if path.startswith("tests/") and any(Path(path).name in r for r in rosters):
        return "a test file in this lane"
    if path in imports:
        return "imported directly by a test in this lane"
    for pattern in patterns:
        if re.search(pattern, path):
            return f"lane surface ({pattern})"
    return ""


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
    hits = [(p, surface_reason(args.gate, p, imports)) for p in paths]
    hits = [(p, why) for p, why in hits if why]
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
