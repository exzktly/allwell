"""Classify a change by what it can affect: version-only, docs-only, or code.

The fast lane is ~11,000 tests, and a change that touches nothing but the version
number or prose cannot change the result of almost any of them. This script says
which of three scopes a change is in, so ``scripts/test.sh``,
``scripts/preflight.sh`` and CI can run only what the change can reach:

``version-only``
    Every changed path is ``version.py``, and every changed line in it is the
    ``__version__ = "..."`` assignment. No test runs and lint is skipped. The CI
    ``version bump`` job still runs, because it is the check that reads that line:
    it fails unless the number moved by exactly one well-formed increment.
``docs-only``
    Every changed path matches ``tests/_lanes.py::DOCS_ONLY_PATTERNS`` or is a
    version-only change to ``version.py``. Lint is skipped and the fast lane
    narrows to the **docs lane**: the test files that read those documents.
``code``
    Anything else, and every case this script cannot decide: no diff (git
    missing, an unknown base ref), or an empty diff. An unanswerable question
    runs everything; it must never quietly skip tests.

**The docs lane is computed, not written down**, so a new test that reads a
document joins it by reading the document. A test file is in it when, outside
its docstrings, a string constant names a docs token (the basename of any file a
docs-only pattern matches, or ``Markdowns`` / ``_Docs``), or when it imports
directly a first-party module whose own string constants do — which is how
``tests/test_help_topics.py`` joins through ``ui/help_topics.py``, the one app
module that reads the manual. Docstrings are excluded because hundreds of tests
cite ``ARCHITECTURE.md`` in prose without opening it. What this cannot see is a
test reaching a document two imports away; CI runs the full fast lane on every
push to ``main``, which is where such a miss is caught, as for the gated lanes.

    python scripts/change_scope.py [--base REF]   # prints the scope; the reason goes to stderr
    python scripts/change_scope.py --docs-tests   # prints the docs lane, one test file per line

``--base`` defaults to ``$ALLWELL_SCOPE_BASE``, else ``origin/main``. The
comparison is ``REF...HEAD`` plus, locally, uncommitted and untracked files
(``lane_needed.changed_paths``).
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
sys.path.insert(0, str(REPO / "scripts"))
from _lanes import DOCS_ONLY_PATTERNS, VERSION_FILE  # noqa: E402
from lane_needed import _imported_names, _module_file, changed_paths  # noqa: E402

VERSION_ONLY = "version-only"
DOCS_ONLY = "docs-only"
CODE = "code"

#: A changed line of version.py that a version-only change may contain.
_VERSION_LINE = re.compile(r'^__version__ = "[^"]*"$')


def is_docs_path(path: str) -> bool:
    return any(re.search(pattern, path) for pattern in DOCS_ONLY_PATTERNS)


def classify(paths: list[str] | None, version_lines_only: bool = True) -> str:
    """The scope of a change to *paths*; *version_lines_only* says version.py's diff touches only ``__version__``.

    ``None`` (git could not say) and an empty list are both ``code``.
    """
    if not paths:
        return CODE
    if VERSION_FILE in paths and not version_lines_only:
        return CODE
    rest = [p for p in paths if p != VERSION_FILE]
    if not rest:
        return VERSION_ONLY
    if all(is_docs_path(p) for p in rest):
        return DOCS_ONLY
    return CODE


def version_diff_lines(base: str, root: Path = REPO) -> list[str] | None:
    """The added and removed lines of version.py against *base* (committed and working tree); None if git fails."""
    lines: list[str] = []
    for args in (["git", "diff", "-U0", f"{base}...HEAD", "--", VERSION_FILE],
                 ["git", "diff", "-U0", "HEAD", "--", VERSION_FILE]):
        try:
            out = subprocess.run(args, cwd=root, capture_output=True, text=True,
                                 encoding="utf-8", check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
        lines += [ln[1:] for ln in out.splitlines()
                  if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return lines


def only_version_assignment(lines: list[str] | None) -> bool:
    """True when every changed line is the ``__version__`` assignment (and git could answer)."""
    return lines is not None and all(_VERSION_LINE.match(ln.strip()) for ln in lines)


def docs_tokens(root: Path = REPO) -> set[str]:
    """Names that, in a string constant, mean code reads a docs-only file."""
    tokens = {"Markdowns", "_Docs"}
    for path in root.rglob("*.md"):
        rel = path.relative_to(root).as_posix()
        if is_docs_path(rel):
            tokens.add(path.name)
    return tokens


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def names_a_doc(tree: ast.Module, tokens: set[str]) -> bool:
    """True when a string constant outside the docstrings of *tree* contains one of *tokens*."""
    skip = _docstring_ids(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                and any(token in node.value for token in tokens)):
            return True
    return False


def docs_lane(root: Path = REPO) -> list[str]:
    """The test files (``tests/test_*.py``, repository-relative) that read a docs-only file, directly or through
    a first-party module they import directly."""
    tokens = docs_tokens(root)
    reads: dict[str, bool] = {}

    def module_reads(rel: str) -> bool:
        if rel not in reads:
            try:
                reads[rel] = names_a_doc(ast.parse((root / rel).read_text(encoding="utf-8")), tokens)
            except (OSError, SyntaxError, UnicodeDecodeError):
                reads[rel] = False
        return reads[rel]

    lane = []
    for test in sorted((root / "tests").glob("test_*.py")):
        rel = test.relative_to(root).as_posix()
        try:
            tree = ast.parse(test.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            lane.append(rel)  # cannot tell what it reads: run it
            continue
        if names_a_doc(tree, tokens):
            lane.append(rel)
            continue
        for name in _imported_names(tree):
            target = _module_file(name, root)
            if target is not None and module_reads(target):
                lane.append(rel)
                break
    return lane


def scope(base: str, root: Path = REPO) -> tuple[str, str]:
    """``(scope, reason)`` for the change on this branch against *base*."""
    paths = changed_paths(base, root)
    if paths is None:
        return CODE, f"could not diff against {base}"
    if not paths:
        return CODE, f"no change against {base}"
    lines = version_diff_lines(base, root) if VERSION_FILE in paths else []
    result = classify(paths, only_version_assignment(lines))
    if result == VERSION_ONLY:
        return result, "only the version number changed"
    if result == DOCS_ONLY:
        return result, f"only documentation changed ({len(paths)} path(s))"
    other = [p for p in paths if not is_docs_path(p) and p != VERSION_FILE]
    if not other:
        return result, f"{VERSION_FILE} changes more than its __version__ line"
    return result, f"{len(other)} changed path(s) are code, e.g. {other[0]}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=os.environ.get("ALLWELL_SCOPE_BASE") or "origin/main")
    parser.add_argument("--docs-tests", action="store_true", help="print the docs lane and exit")
    args = parser.parse_args(argv)
    if args.docs_tests:
        print("\n".join(docs_lane()))
        return 0
    result, reason = scope(args.base)
    print(result, flush=True)  # before the reason, for a caller reading both from one pipe
    print(f"change scope: {result} — {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
