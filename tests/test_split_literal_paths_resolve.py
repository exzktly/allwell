"""A path built from several string literals must still name a file that exists.

`repo_root() / "scripts" / "remote_bootstrap.py"` is one path written as two
literals, and a sweep that rewrites module paths cannot see it: the string
`scripts/remote_bootstrap.py` never appears anywhere in the file, so a search
for it finds nothing and reports the sweep complete. Move the module and the
basename follows while the directory does not, leaving a path that has never
existed.

This has now happened twice on one branch. The PyInstaller spec bundles
`(source, destination)` as two literals and only the source followed the file.
`analyze/remote_dialog.py` built the bootstrap path from `"scripts"` and
`"remote_bootstrap.py"` and only the second followed — under a comment
describing the *previous* time the same line pointed at a directory that had
never held the file.

**What this checks, and why it cannot fire on a path that was never local.** A
site is an offender only when the basename it names *is* a file in this
repository and the fuller path it wrote is not. A remote path
(`~/allwell/requirements-remote.txt`), a temporary directory, or anything whose
basename is not a file here is ignored, because the premise "this was meant to
point at a repository file" is unproven for those. That makes the check
conservative in exactly the direction that matters: it accuses only where the
file is demonstrably present under a different directory.
"""
from __future__ import annotations

import ast
from collections import defaultdict

from tests._repo_scan import REPO_ROOT, parse_source, python_sources


def _segments(node: ast.AST) -> list[str] | None:
    """The trailing string literals of a `a / "b" / "c.py"` chain, outermost last.

    Returns None when the chain is not made of plain string constants, since a
    computed segment could be anything and this check has nothing to say about it.
    """
    out: list[str] = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        if not (isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str)):
            return None
        out.insert(0, cur.right.value)
        cur = cur.left
    return out or None


def _repo_files_by_name() -> dict[str, list[str]]:
    """Every tracked Python file in the repository, keyed by basename."""
    out: dict[str, list[str]] = defaultdict(list)
    for p in REPO_ROOT.rglob("*.py"):
        if "__pycache__" in p.parts or ".git" in p.parts:
            continue
        out[p.name].append(str(p.relative_to(REPO_ROOT)))
    return out


def test_a_path_written_as_several_literals_still_exists():
    by_name = _repo_files_by_name()
    offenders: list[str] = []

    for path in python_sources():
        rel = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(parse_source(path)):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            segs = _segments(node)
            if not segs or len(segs) < 2 or not segs[-1].endswith(".py"):
                continue
            homes = by_name.get(segs[-1])
            if not homes:
                continue  # not a file in this repository; nothing to claim
            suffix = "/".join(segs)
            if any(h == suffix or h.endswith("/" + suffix) for h in homes):
                continue
            offenders.append(
                f"{rel}:{node.lineno} builds …/{suffix}, but {segs[-1]} is at "
                + " or ".join(homes))

    assert not offenders, (
        "these paths are written as several string literals and name a "
        "directory the file is not in:\n  " + "\n  ".join(sorted(set(offenders)))
        + "\n\nThe module moved and only the basename literal followed. A "
        "textual sweep cannot see this, because the joined path never appears "
        "in the source — so nothing but this check reports it, and the symptom "
        "is a file-not-found at whatever moment the path is first used.")
