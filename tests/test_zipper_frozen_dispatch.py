"""Guardrail: the well_plate_zipper subprocess must use the frozen dispatch.

In a frozen (PyInstaller) bundle ``sys.executable`` is the .app binary, not a
Python interpreter, so ``[sys.executable, "pipeline/well_plate_zipper.py", ...]`` would
just relaunch the GUI and the grouping phase would never run — the analysis
pipeline fails when built as a binary. ``build_zipper_cmd`` must instead emit
the ``--run-zipper`` sentinel (which all_well_launcher routes into
well_plate_zipper.main()), mirroring the pipeline's ``--run-pipeline`` path.

This is a frozen-only failure mode that a normal (unfrozen) CI run of the app
can't reproduce, so it's pinned here at the command-build layer.
"""

import ast
import sys
from pathlib import Path

from services.input_resolution_service import build_zipper_cmd
from tests._repo_scan import REPO_ROOT, parse_source

_ZIP = Path("/bundle/pipeline/well_plate_zipper.py")
_TIF = Path("/data/tifs")
_OUT = Path("/data/in")


def _cmd(**kw):
    return build_zipper_cmd(_ZIP, _TIF, _OUT, "well:field:z:c", "_", **kw)


def test_source_mode_invokes_the_script_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = _cmd()
    assert cmd[0] == sys.executable
    assert cmd[1] == str(_ZIP)          # runs python on the .py file
    assert "--run-zipper" not in cmd


def test_frozen_mode_uses_run_zipper_sentinel(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = _cmd()
    # Sentinel must be argv[1] so the launcher's ``argv[1] == "--run-zipper"``
    # dispatch fires, and the .py path must NOT be passed (it would relaunch
    # the GUI in a real bundle).
    assert cmd[:2] == [sys.executable, "--run-zipper"]
    assert str(_ZIP) not in cmd
    assert not any(str(a).endswith("pipeline/well_plate_zipper.py") for a in cmd)


def test_args_are_forwarded_in_both_modes(monkeypatch):
    for frozen in (False, True):
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        cmd = _cmd(move_source_tifs=True)
        assert "--search-dir" in cmd and str(_TIF) in cmd
        assert "--output-dir" in cmd and str(_OUT) in cmd
        assert cmd[cmd.index("--filename_schema") + 1] == "well:field:z:c"
        assert cmd[cmd.index("--filename_sep") + 1] == "_"
        assert "--move-source-tifs" in cmd


# ── the other half of the dispatch: the module it imports must exist ─────────
#
# `build_zipper_cmd` emits the sentinel; the launcher has to route it somewhere
# real. That import is the half no test looked at, and it is the half a module
# move breaks: when the zipper became `pipeline/well_plate_zipper.py`, the
# launcher's `import WellPlateZipper` named a module that no longer existed.
# Nothing would have said so — the branch runs only in a frozen bundle, reached
# only by a user grouping a plate, and `all_well_launcher` is the one module no
# test may import (doing so installs a setuptools stub and runs the _MEIPASS
# bootstrap, reshaping the interpreter for every later test). So this is checked
# the way the rest of the launcher is: against its syntax tree.

def _dispatch_imports() -> dict[str, list[str]]:
    """Every module imported inside `all_well_launcher._dispatch`, per sentinel.

    Keyed by the `--run-*` sentinel whose branch the import sits in, so a
    failure names the user-facing path that would break rather than a line
    number. Branches are `if` statements comparing `sys.argv[1]` to a literal.

    `from P import X` is recorded as the dotted `P.X` whether X is a submodule or
    a symbol; :func:`_resolves` is what knows the difference. Deciding it here by
    asking whether `P/X.py` exists is what the first version did, and it made the
    check unable to fail: a *moved* submodule looks exactly like a symbol, so it
    was recorded as plain `P`, which exists, and the import passed.
    """
    tree = parse_source(REPO_ROOT / "all_well_launcher.py")
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_dispatch"), None)
    assert fn is not None, (
        "no `_dispatch` function in all_well_launcher.py — the frozen-dispatch "
        "entry point was renamed and this guard is measuring nothing")

    out: dict[str, list[str]] = {}
    for branch in [n for n in fn.body if isinstance(n, ast.If)]:
        sentinels = [c.value for c in ast.walk(branch.test)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str)
                     and c.value.startswith("--run-")]
        if not sentinels:
            continue
        mods: list[str] = []
        for node in ast.walk(branch):
            if isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods += [f"{node.module}.{a.name}" for a in node.names]
        out[sentinels[0]] = mods
    return out


#: Top-level names that belong to this repository. A dotted import whose first
#: segment is not one of these is stdlib or third-party, and whether a file of
#: that path exists here says nothing about it — `importlib.util` is the one that
#: made this explicit, having been read as a missing `util` submodule.
_FIRST_PARTY = frozenset(
    [d.name for d in REPO_ROOT.iterdir()
     if d.is_dir() and (d / "__init__.py").is_file()]
    + [p.stem for p in REPO_ROOT.glob("*.py")]
)


def _is_module(dotted: str) -> bool:
    """Does this dotted name name a real module or package file in the repo?"""
    base = REPO_ROOT.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _defines(module: str, name: str) -> bool:
    """Does first-party *module* bind *name* at its top level?

    Import, assignment, function and class bindings all count — this only has to
    answer "would `from module import name` find something", not what kind of
    thing it is.
    """
    path = REPO_ROOT.joinpath(*module.split("."))
    path = path.with_suffix(".py") if path.with_suffix(".py").is_file() else path / "__init__.py"
    if not path.is_file():
        return False
    for node in parse_source(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((a.asname or a.name.split(".")[0]) == name for a in node.names):
                return True
    return False


def _resolves(dotted: str) -> bool:
    """Can this first-party import find anything?

    Two ways for `P.X` to be valid: `P.X` is itself a module, or `P` is a module
    that binds the name `X` at its top level. Checking only the first makes a
    moved submodule indistinguishable from a symbol import; checking only the
    second misses a plain `import P.X`. A name that is not first party is outside
    this check's domain and passes — the question is whether a module *this
    repository owns* has moved without the launcher following, not whether the
    interpreter can import the stdlib.

    **The blind spot**, stated because it is why the zipper branch carries a
    second assertion: a first-party *top-level* module that has gone leaves a name
    this cannot judge. `import WellPlateZipper`, the exact line the move left
    stale, is indistinguishable from a third-party package that is not installed
    here, and flagging every unknown top-level import would flag `PySide6` and
    `numpy`. So `test_the_launcher_routes_run_zipper_into_a_module_that_exists`
    also asserts *positively* that the branch imports something named
    `well_plate_zipper`, and that is what fires on the stale line.
    `tests/test_guards_bite.py::test_the_zipper_branch_is_pinned_by_name_not_only_by_resolution`
    holds both halves of that arrangement.
    """
    if dotted.split(".")[0] not in _FIRST_PARTY:
        return True
    if _is_module(dotted):
        return True
    parent, _, leaf = dotted.rpartition(".")
    return bool(parent) and _is_module(parent) and _defines(parent, leaf)


def test_the_launcher_routes_run_zipper_into_a_module_that_exists():
    """The `--run-zipper` branch must import the zipper at its real path."""
    imports = _dispatch_imports()
    assert "--run-zipper" in imports, (
        "all_well_launcher._dispatch has no `--run-zipper` branch, so the "
        "sentinel build_zipper_cmd emits in a frozen bundle routes nowhere and "
        "the grouping phase relaunches the GUI instead")
    named = imports["--run-zipper"]
    assert any(m.endswith("well_plate_zipper") for m in named), (
        f"the `--run-zipper` branch imports {named}, none of which is the "
        "zipper — a frozen grouping run would dispatch to the wrong module")
    unresolved = sorted(m for m in named if not _resolves(m))
    assert not unresolved, (
        f"the `--run-zipper` branch imports {unresolved}, which do not exist in "
        "the repository. The module moved and the launcher did not follow; in a "
        "frozen bundle this raises ModuleNotFoundError inside the subprocess, "
        "where the only symptom is a grouping phase that dies with no output.")


def test_every_launcher_sentinel_imports_something_real():
    """The same rule for the other dispatch branches, so none rots alone.

    `--run-pipeline` and `--run-bootstrap` break identically and just as
    quietly. `_bootstrap_lib` is loaded by file path rather than imported, so a
    branch that names no module at all is not a failure here — an import naming
    a module that has gone is.
    """
    broken = {sentinel: sorted(m for m in mods if not _resolves(m))
              for sentinel, mods in _dispatch_imports().items()}
    broken = {k: v for k, v in broken.items() if v}
    assert not broken, (
        "these frozen-dispatch branches import modules that do not exist: "
        + "; ".join(f"{k} → {v}" for k, v in broken.items()))
