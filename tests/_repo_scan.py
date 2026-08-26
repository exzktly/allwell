"""One definition of "every Python source file in the app" for the AST guards.

Three guards landed in the same branch with three different scan surfaces —
12 root modules, 4 package roots + 3 root modules, and 5 package roots with no
root modules at all. Each was written for the defect in front of it, and each
was therefore blind somewhere another one could see: the divider guard could not
see `analyze_tab_chrome.py`, which builds dividers, and the well-token guard
could not see `pipeline/` or `process_microscopy.py`, which are the call sites
its own docstring cites as the reason the rule exists.

A hand-written roster of what to scan rots exactly the way
`test_architecture_contracts.PIPELINE_FILES` did before this branch replaced it
with a directory walk. So the surface is derived here, once: every package
directory that holds application code, plus every module at the repository root
that is not a script, a test, or a build artifact.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Package directories holding application code. `tests/` is excluded (a test
#: may build the very thing a guard forbids in order to measure it), and
#: `_Docs/`, `design/`, `assets/`, `profiles/` hold no Python.
#:
#: `scripts/` used to be excluded as "dev tooling, not shipped". That was not
#: true of all of it: `scripts/_bootstrap_lib.py` is 1,225 lines, is listed in
#: `_Docs/_Installation/build_all_well.sh`, ships inside the bundle, and runs
#: at runtime — `all_well_launcher._dispatch` loads it by file path when the
#: Remotes dialog provisions a host. Every guard built on this module was
#: therefore blind to it. The rest of `scripts/` is genuinely dev tooling and
#: costs nothing to scan alongside it.
PACKAGE_ROOTS = (
    "well_viewer",
    "widgets",
    "datasets",
    "services",
    "pipeline",
    "util",
    "ui",
    "scripts",
)

#: Root-level modules that are application code. Derived by exclusion rather
#: than listed by hand, so a new one is covered the day it appears.
_ROOT_EXCLUDE = {"conftest.py", "setup.py"}


def root_modules() -> list[Path]:
    return sorted(
        p for p in REPO_ROOT.glob("*.py")
        if p.name not in _ROOT_EXCLUDE and not p.name.startswith("test_")
    )


def python_sources(*, roots: tuple[str, ...] = PACKAGE_ROOTS,
                   include_root_modules: bool = True) -> list[Path]:
    """Every application source file, deduplicated and stably ordered."""
    out: list[Path] = []
    for root in roots:
        directory = REPO_ROOT / root
        if not directory.is_dir():
            continue
        out.extend(p for p in sorted(directory.rglob("*.py"))
                   if "__pycache__" not in p.parts)
    if include_root_modules:
        out.extend(root_modules())
    return out


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# ── Parsing ──────────────────────────────────────────────────────────────────
#
# Parsing the tree costs about 2.8 s (479 files, 7 MB, 767k nodes), and roughly
# sixty guards in the fast lane each want the whole of it. Parsed once per
# process rather than once per guard, that is three minutes of the lane's wall
# clock — it was the second-largest cost in it, behind the widgets every test
# used to leave behind.
#
# The cache is keyed on the source *text*, not on the path or its stat.
# Reading the whole tree costs 0.026 s against 2.8 s to parse it, so re-reading
# every time is close to free — and it is the only key that cannot go stale.
# `tests/test_guards_bite.py` rewrites a synthetic file in place between cases;
# a path+mtime+size key answers those from the previous parse whenever the
# filesystem's timestamp granularity is coarser than the gap between the two
# writes and the sizes happen to match, and a guard silently measuring the file
# it was handed one case ago is exactly the "green for the wrong reason" this
# module's guards exist to prevent.
#
# The trees are SHARED. Read them; never mutate one. No guard does today —
# nothing in `tests/` assigns to a node, transforms a tree, or hangs a
# `.parent` off it — and `tests/test_guards_bite.py` keeps the cache honest
# about handing back the same object.


@lru_cache(maxsize=None)
def _parse_text(source: str, filename: str) -> ast.Module:
    return ast.parse(source, filename=filename)


def parse_source(path: Path | str) -> ast.Module:
    """The parsed module at ``path``, from a per-process cache.

    Use this instead of ``ast.parse(path.read_text())`` anywhere a guard walks
    a file that belongs to the repository. Sources the test *builds* — the
    synthetic violations in ``test_guards_bite.py``, a snippet in a docstring —
    are one-offs with nothing to share, and go on calling ``ast.parse``
    directly.
    """
    path = Path(path)
    return _parse_text(path.read_text(encoding="utf-8"), str(path))


def parsed_sources(*, roots: tuple[str, ...] = PACKAGE_ROOTS,
                   include_root_modules: bool = True,
                   ) -> list[tuple[Path, ast.Module]]:
    """Every application source file paired with its parsed tree."""
    return [(p, parse_source(p))
            for p in python_sources(roots=roots,
                                    include_root_modules=include_root_modules)]
