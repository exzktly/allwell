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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Package directories holding application code. `scripts/` is excluded (dev
#: tooling, not shipped), `tests/` is excluded (a test may build the very thing
#: a guard forbids in order to measure it), and `_Docs/`, `design/`, `assets/`,
#: `profiles/` hold no Python.
PACKAGE_ROOTS = (
    "well_viewer",
    "widgets",
    "datasets",
    "services",
    "pipeline",
    "util",
    "ui",
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
