"""Guard: every top-level package of application code is in ``PACKAGE_ROOTS``.

``tests/_repo_scan.py`` is the one definition of "every Python source file in
the app", and roughly sixty AST guards read their file list from it. A package
missing from ``PACKAGE_ROOTS`` is therefore not a small gap: every one of those
guards stops seeing the code inside it, all at once and without a word.

That failure is invisible in the worst possible way — it makes the suite
*greener*. Moving the Analyze pane into ``analyze/`` took the scan surface from
511 files to 497 while every test still passed, because the fourteen files that
left the repository root had nowhere to be found again. Nothing measures a guard
that walks past code it was never handed, so the only signal available is the
one this test provides: comparing the roots that exist on disk against the roots
the scanner has been told about.

The rule is deliberately shaped as "a directory holding application code",
because that is the thing the guards care about — not whether it has an
``__init__.py``, since a namespace package would be just as invisible.
"""
from __future__ import annotations

from tests._repo_scan import PACKAGE_ROOTS, REPO_ROOT

#: Top-level directories that hold Python but are not application code.
#:
#: ``tests`` is excluded by ``_repo_scan``'s own contract — a test may build the
#: very thing a guard forbids in order to measure it. ``_Docs`` holds no app
#: code either: its only Python is the four PyInstaller hooks under
#: ``_Docs/_Installation/hooks/``, which run inside the build's own interpreter
#: and never ship.
_NOT_APP_CODE = {"tests", "_Docs"}

#: Directory names that are never source, wherever they appear.
_NOISE = {"__pycache__", ".git", "build", "dist", "node_modules"}


def _top_level_python_dirs() -> set[str]:
    """Every top-level directory holding at least one ``.py`` file."""
    found: set[str] = set()
    for entry in REPO_ROOT.iterdir():
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in _NOISE:
            continue
        for path in entry.rglob("*.py"):
            if not any(part in _NOISE for part in path.parts):
                found.add(entry.name)
                break
    return found


def test_every_application_package_is_scanned():
    missing = sorted(_top_level_python_dirs() - set(PACKAGE_ROOTS) - _NOT_APP_CODE)
    assert not missing, (
        "Top-level package(s) holding application code that no AST guard can "
        "see, because they are absent from PACKAGE_ROOTS in "
        f"tests/_repo_scan.py:\n  {', '.join(missing)}\n\n"
        "Add each to PACKAGE_ROOTS, or to _NOT_APP_CODE here with the reason "
        "it is not application code. Leaving it out does not fail anything — "
        "it silently retires every guard over that package."
    )


def test_every_scanned_root_still_exists():
    """A stale entry is the mirror image, and it fails open just as quietly.

    ``python_sources`` skips a root that is not a directory, so a package that
    was renamed or deleted leaves an entry that quietly matches nothing rather
    than raising. The roster then describes a tree that no longer exists.
    """
    stale = sorted(r for r in PACKAGE_ROOTS if not (REPO_ROOT / r).is_dir())
    assert not stale, (
        "PACKAGE_ROOTS names director(ies) that do not exist, so they "
        f"contribute no files and nothing says so:\n  {', '.join(stale)}"
    )
