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

#: Python that is not named `.py`, and is therefore outside `python_sources()`.
#: The spec is the reason this guard exists — it bundles `(source, destination)`
#: as two literals — so leaving it unscanned would have made the guard blind to
#: its own motivating incident. `tests/test_scan_surface_is_complete.py` guards
#: `PACKAGE_ROOTS`; this is one file, named here because it has no package.
_EXTRA_SOURCES = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec",)


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


#: Directories whose contents are not repository content.
_IGNORED_DIRS = {".git", "__pycache__", "build", "dist", ".venv", "venv",
                 "node_modules", ".ruff_cache", ".pytest_cache"}

#: Test fixtures are excluded from the basename index, and the reason is the
#: guard's whole premise. That premise is "this basename names a file in the
#: repository, so the path was meant to point at one" — and a fixture is a
#: *copy of a runtime artifact*, not a place anything lives. `pipeline_info.json`
#: exists under `tests/fixtures/`, so without this the guard accuses
#: `datasets/subset/view.py` of misnaming the `<dataset>/out/pipeline_info.json`
#: a user's own dataset holds, which is not a repository path at all. Indexing
#: fixtures would make the guard loudest exactly where it knows least.
_IGNORED_PREFIXES = ("tests/fixtures/",)


def _repo_files_by_name() -> dict[str, list[str]]:
    """Every file in the repository, keyed by basename.

    Not just `.py`: the incident that motivated this guard was a `.txt`
    manifest bundled to a stale destination, and restricting the scan to
    Python would have excluded it. A basename that names no repository file is
    ignored either way, so widening costs nothing but catches the manifests,
    the shell scripts and the spec.
    """
    out: dict[str, list[str]] = defaultdict(list)
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file() or any(d in p.parts for d in _IGNORED_DIRS):
            continue
        rel = str(p.relative_to(REPO_ROOT))
        if rel.startswith(_IGNORED_PREFIXES):
            continue
        out[p.name].append(rel)
    return out


def test_a_path_written_as_several_literals_still_exists():
    by_name = _repo_files_by_name()
    offenders: list[str] = []

    for path in (*python_sources(), *_EXTRA_SOURCES):
        rel = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(parse_source(path)):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            segs = _segments(node)
            if not segs or len(segs) < 2:
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
