"""Guard: the PyInstaller spec's rosters match the tree they claim to bundle.

`_Docs/_Installation/all_well.spec` names first-party code twice — the
`sibling_scripts` list of repository-root modules, and the `for _pkg in (...)`
loop over first-party packages — and both had drifted from the tree.

Neither drift could produce a test failure. The frozen build is not exercised
anywhere in this suite, so the spec is the one part of the repository whose
mistakes are only ever discovered by a user running a released binary. Worse,
a gap there is not reliably a bug *yet*: PyInstaller's own module graph
collects whatever today's static imports reach, so an omitted module can be
fine for a year and then break the moment someone adds an `importlib` call
inside the package that reaches it. The failure then lands in a released
binary, on the screen that uses it, with a green suite behind it.

So the rule these tests hold is "ship all of it", not "ship what the graph
might miss" — the second is a judgement about today's imports wearing the
costume of a rule about modules. Being wrong in the shipping direction costs
kilobytes of duplicated source. Being wrong in the other direction costs a
release.

Deriving the rosters from the spec's own AST rather than importing it: the
spec is not importable outside a PyInstaller build (it calls `Analysis`,
`pkg_dir`, and friends at module scope).
"""
from __future__ import annotations

import ast
import sys

from tests._repo_scan import PACKAGE_ROOTS, REPO_ROOT, parse_source

SPEC = REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec"

#: Root modules the spec is right not to list, with the reason.
_ROOT_EXCUSED = {
    # The Analysis entry script — it becomes the executable, not a data file.
    "all_well_launcher.py": "the PyInstaller entry script, not a data file",
}

#: Packages the loop is right not to cover, with the reason.
_PKG_EXCUSED = {
    # Dev tooling, except `_bootstrap_lib.py`, which the spec ships by name
    # into `scripts/` so its `REPO_ROOT = __file__.parent.parent` lands on the
    # bundle root. Shipping the rest would bundle the test harness.
    "scripts": "dev tooling; _bootstrap_lib.py is shipped by name instead",
}


def _spec_tree() -> ast.Module:
    # Through `parse_source`, not `ast.parse`: it is a repository file, the
    # tree is shared per process, and each test here walks it more than once.
    # Read only — the cached tree is handed to every caller.
    return parse_source(SPEC)


def _path_parts(node: ast.AST) -> list[str] | None:
    """The string segments of a `_parent / "a" / "b.py"` expression.

    Reconstructed whole rather than by basename: entries are not all
    top-level. `scripts/_bootstrap_lib.py` is two segments, and reading only
    the last one turns it into a root module that does not exist.
    """
    if isinstance(node, ast.Name):
        return [] if node.id == "_parent" else None
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)):
        return None
    left = _path_parts(node.left)
    return None if left is None else left + [node.right.value]


def _sibling_script_names() -> set[str]:
    """The `.py` paths in the `sibling_scripts = [...]` literal, repo-relative."""
    for node in ast.walk(_spec_tree()):
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "sibling_scripts"
                        for t in node.targets)):
            continue
        out = set()
        for n in ast.walk(node.value):
            parts = _path_parts(n) if isinstance(n, ast.BinOp) else None
            if parts and parts[-1].endswith(".py"):
                out.add("/".join(parts))
        return out
    raise AssertionError("no `sibling_scripts = [...]` assignment in the spec")


def _bundled_packages() -> set[str]:
    """The tuple the `for _pkg in (...)` package loop iterates."""
    for node in ast.walk(_spec_tree()):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == "_pkg"
                and isinstance(node.iter, ast.Tuple)):
            return {e.value for e in node.iter.elts
                    if isinstance(e, ast.Constant)}
    raise AssertionError("no `for _pkg in (...)` package loop in the spec")


def _app_root_modules() -> set[str]:
    """Repository-root modules that are application code."""
    return {p.name for p in REPO_ROOT.glob("*.py")
            if p.name not in {"conftest.py", "setup.py"}
            and not p.name.startswith("test_")}


def test_the_spec_ships_every_root_module():
    listed_at_root = {n for n in _sibling_script_names() if "/" not in n}
    missing = sorted(_app_root_modules() - listed_at_root
                     - set(_ROOT_EXCUSED))
    assert not missing, (
        "these repository-root modules are application code that the spec "
        "does not bundle:\n  " + ", ".join(missing) + "\n\n"
        "Add each to `sibling_scripts`, or to _ROOT_EXCUSED here with the "
        "reason. A module reaching the bundle today through PyInstaller's "
        "module graph is not a reason — that holds only while every import "
        "that reaches it stays static, and nothing here would notice it "
        "stopping.")


def test_the_spec_bundles_every_first_party_package():
    missing = sorted(set(PACKAGE_ROOTS) - _bundled_packages() - set(_PKG_EXCUSED))
    assert not missing, (
        "these first-party packages are not bundled by the spec's package "
        f"loop: {', '.join(missing)}. Add each to the loop, or to _PKG_EXCUSED "
        "here with the reason. Under an active profile the loop is also what "
        "applies the per-file exclusion filter, so an absent package ships "
        "unfiltered as well as unlisted.")


def test_the_spec_names_nothing_that_has_gone():
    """The mirror image: an entry for a file or package that no longer exists.

    `_pkg_datas` skips a package path that is not a directory and the sibling
    list is built with `str(...)` on a path nobody checks, so a stale entry
    contributes nothing and says nothing — the same fail-open shape the rest
    of this file guards.
    """
    stale_files = sorted(n for n in _sibling_script_names()
                         if not (REPO_ROOT / n).is_file())
    stale_pkgs = sorted(p for p in _bundled_packages()
                        if not (REPO_ROOT / p).is_dir())
    assert not stale_files and not stale_pkgs, (
        "the spec names first-party code that does not exist, so it bundles "
        f"nothing and reports nothing — files: {stale_files or 'none'}; "
        f"packages: {stale_pkgs or 'none'}")


#: External top-level packages the spec is allowed to import, with the reason.
#:
#: Everything not stdlib and not listed here must resolve to a file in this
#: repository. That default is deliberately inverted from the obvious one: the
#: first version of this guard skipped any import whose head was not a known
#: first-party name, which made it blind to precisely the case it exists for —
#: a module that has *gone* is no longer a first-party name either, so the stale
#: `import theme` it was written to catch sailed straight through it. The spec's
#: universe of legitimate external imports is small and enumerable, so the
#: allowlist can be the thing that carries a written reason instead.
_EXTERNAL_OK = {
    "PyInstaller": "the build tool executing this spec (collect_all, hooks)",
    "tomli": "the 3.10 build environment's backport of tomllib",
}


def _is_module(dotted: str) -> bool:
    base = REPO_ROOT.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _spec_repo_imports() -> list[tuple[int, str]]:
    """Every import the spec makes that must resolve inside this repository.

    `from P import a, b` is recorded as `P` — the spec imports *symbols* out of
    `scripts.profile_build` and `services.remote_runner`, and the question here is
    only whether the module it reaches into still exists.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(_spec_tree()):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append((node.lineno, node.module))
    return [(ln, m) for ln, m in out
            if m.split(".")[0] not in sys.stdlib_module_names
            and m.split(".")[0] not in _EXTERNAL_OK]


def test_the_spec_imports_only_modules_that_exist():
    """The spec's own imports, held against the tree.

    The two roster tests above read what the spec *bundles*. They walk straight
    past what it *imports*, and the spec imports first-party code at module scope
    to do its job — `scripts.profile_build` for the profile maths,
    `services.remote_runner` for the upload manifest, `ui.theme` to validate a
    baked `ALLWELL_THEME` name.

    A stale one of those is not a missing file in the bundle, it is a build that
    does not start: PyInstaller executes the spec as Python, so the import raises
    and the run aborts. The `ALLWELL_THEME` import is the one that proved it — it
    sits inside `if _BAKED_THEME:`, so only a themed build (`ALLWELL_THEME=grey
    scripts/build_executable.sh`) reaches it, and the default build stays green
    while the themed one is broken. Nothing in this suite executes the spec, so
    without this check the first symptom is a release that will not build.
    """
    missing = [(ln, m) for ln, m in _spec_repo_imports() if not _is_module(m)]
    assert not missing, (
        "the PyInstaller spec imports first-party modules that no longer exist, "
        "so PyInstaller aborts while executing it:\n  "
        + "\n  ".join(f"{SPEC.name}:{ln} — {m}" for ln, m in missing)
        + "\n\nThe module moved and the spec did not follow. Note that an import "
        "inside a conditional branch (the ALLWELL_THEME block) breaks only the "
        "builds that take it.")
