"""Decide whether a change needs the UI lane (``scripts/test.sh --ui``).

The UI lane holds the layout and appearance tests listed in
``tests/_lanes.py``. It runs only when a change could affect them, so this
script answers one question: does any changed path fall in the UI surface?

The surface is ``tests/_lanes.py::UI_SURFACE_PATTERNS`` plus every first-party
module a roster file imports directly, read from the test files' own syntax
trees each time this runs. Computing the second half rather than writing it
down means a new UI test that imports a new module extends the trigger by
being added to the roster, with nothing else to remember.

    python scripts/ui_lane.py needed [--base REF]   # exit 0: run it; 1: skip
    python scripts/ui_lane.py surface               # print the computed module set

``needed`` compares ``REF...HEAD`` (default ``origin/main``) and, locally, the
uncommitted working tree as well. When the comparison cannot be made at all —
an unknown ref, no git — it answers "run it": an unanswerable question must
not quietly skip a lane. Read by ``scripts/preflight.sh`` and by the CI job.
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
from _lanes import UI_LANE_FILES, UI_SURFACE_PATTERNS  # noqa: E402


def _module_file(name: str) -> str | None:
    """The repository-relative file a dotted first-party module name resolves to."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        base = REPO.joinpath(*parts[:i])
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py").relative_to(REPO).as_posix()
        if (base / "__init__.py").is_file():
            return (base / "__init__.py").relative_to(REPO).as_posix()
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


def roster_imports() -> set[str]:
    """First-party files the UI-lane test files import directly (tests/ excluded)."""
    files: set[str] = set()
    for name in UI_LANE_FILES:
        tree = ast.parse((REPO / "tests" / name).read_text(encoding="utf-8"))
        for mod in _imported_names(tree):
            if mod.split(".")[0] == "tests":
                continue
            path = _module_file(mod)
            if path is not None:
                files.add(path)
    return files


def surface_reason(path: str, imports: set[str]) -> str:
    """Why *path* triggers the UI lane, or ``""`` when it does not."""
    if path.startswith("tests/") and Path(path).name in UI_LANE_FILES:
        return "a UI-lane test file"
    if path in imports:
        return "imported directly by a UI-lane test"
    for pattern in UI_SURFACE_PATTERNS:
        if re.search(pattern, path):
            return f"UI surface ({pattern})"
    return ""


def changed_paths(base: str) -> list[str] | None:
    """Paths changed on this branch against *base*, plus the working tree; None if git cannot say."""
    try:
        committed = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"], cwd=REPO,
            capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
        working = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], cwd=REPO,
            capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO,
            capture_output=True, text=True, encoding="utf-8", check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(set(committed) | set(working) | set(untracked))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    needed = sub.add_parser("needed")
    needed.add_argument("--base", default="origin/main")
    sub.add_parser("surface")
    args = parser.parse_args(argv)

    imports = roster_imports()
    if args.cmd == "surface":
        print("\n".join(sorted(imports)))
        return 0

    paths = changed_paths(args.base)
    if paths is None:
        print(f"UI lane: needed (could not diff against {args.base})")
        return 0
    hits = [(p, surface_reason(p, imports)) for p in paths]
    hits = [(p, why) for p, why in hits if why]
    if hits:
        print(f"UI lane: needed — {len(hits)} changed path(s) in the UI surface:")
        for path, why in hits[:15]:
            print(f"   {path}  [{why}]")
        if len(hits) > 15:
            print(f"   … and {len(hits) - 15} more")
        return 0
    print(f"UI lane: not needed — none of {len(paths)} changed path(s) is in the UI surface")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
