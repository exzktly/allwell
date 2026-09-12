"""Guard: the repository root holds entry points, config and packages. Nothing else.

The root listing is the first thing anyone reads about a codebase, and for a
long time this one answered no question at all. It carried four executable
entry points, five ordinary library modules, the project config and fourteen
packages, with no stated rule separating them — so `theme.py` (the design-token
module every widget in the application reads) sat beside `allwell.py` (the
program you run), and `datasets_tab.py` sat beside the `datasets/` package whose
seven features it hosts. Worse, the misplacement pointed the wrong way: a reader
looking for the theme found `ui/theme/`, a 48-line remnant holding two legacy
colours, and had no reason to suspect the real module was somewhere else
entirely.

None of that could fail. A loose module imports exactly as well as a packaged
one, so the only cost is paid by whoever has to find it — which is why the
drift is one-way and why it needs a test rather than a convention.

**The rule.** A `.py` file at the repository root is one of two things:

* an **entry point a person is meant to type** — `python allwell.py`, the
  pipeline CLI, and the module the frozen bundle boots (which is the executable
  itself). Being the documented way to start the software is the reason it is
  findable at the top, and burying it at `tools/cli/run.py` would make the thing
  you invoke harder to find rather than easier.

  "Something gets `exec`'d" is *not* the test, and reading it that way is how
  `pipeline/well_plate_zipper.py` spent a long time at the root. A helper the
  application spawns behind the user's back is an implementation detail of
  whatever spawns it: nobody types it, nobody needs to find it, and it belongs
  in the package whose work it does. Being spawned by path only means the module
  has to put the repository root on `sys.path` itself, which is four lines;
* **repository metadata** — a single declaration that belongs to the repository
  rather than to any package, the way `pyproject.toml` does. `version.py` is the
  only one, and it is read from outside Python entirely: `scripts/preflight.sh`
  and the PyInstaller spec both parse it by path.

Everything else — a module with an importer and no `__main__`, a widget, a map,
a helper — belongs in a package, where the directory it sits in says what it is.

Both halves are checked. A root module missing from the roster fails, and so
does a roster entry naming a file that has gone: an allowlist nobody prunes
becomes a list of permissions for files that no longer exist, which is how the
next module gets waved through under the name of one that left. The same
applies to `_Docs/_Installation/build_all_well.sh`'s preflight roster, which
demonstrated the failure before this test existed — it went on requiring
`analyze_tab.py` for however long it had been since the Analyze pane became
`analyze/tab.py`, and since a missing required file aborts the script, the
build could not have run at all.
"""
from __future__ import annotations

import re

from tests._repo_scan import REPO_ROOT

#: Every `.py` allowed at the repository root, with what entitles it to be there.
#:
#: A reason is not decoration. A module left at the root deliberately and one
#: nobody got round to moving look identical in a directory listing six months
#: later, so the entry is the place the distinction is written down. "It has
#: always been there" is not a reason; neither is "moving it would touch a lot
#: of files".
ROOT_MODULES = {
    "allwell.py":
        "entry point — `python allwell.py` starts the GUI; also the title-bar "
        "shell and the help drawer",
    "process_microscopy.py":
        "entry point — the pipeline orchestrator and its CLI, run directly "
        "(`python process_microscopy.py --input_dir … --output_dir …`) and "
        "spawned as a subprocess by the Analyze pane and on a remote worker",
    "all_well_launcher.py":
        "entry point — the module PyInstaller boots in the frozen bundle "
        "(`_Docs/_Installation/all_well.spec` names it as the Analysis script), "
        "and the `--run-pipeline` / `--run-bootstrap` subprocess dispatcher",
    "version.py":
        "repository metadata — the one version literal, parsed by path from "
        "outside Python by `scripts/preflight.sh` and the PyInstaller spec, so "
        "it cannot move into a package without both learning where it went",
}

#: The build script's own preflight roster, and where to find it.
_BUILD_SCRIPT = REPO_ROOT / "_Docs" / "_Installation" / "build_all_well.sh"

#: `    "some/path.py"` inside the `REQUIRED=( … )` array.
_REQUIRED_ENTRY = re.compile(r'^\s*"([^"]+)"\s*$')


def _root_py_files() -> set[str]:
    """Every `.py` at the repository root that is not the pytest bootstrap."""
    return {p.name for p in REPO_ROOT.glob("*.py")
            if p.name not in {"conftest.py", "setup.py"}}


def _build_script_required() -> list[str]:
    """The paths in `build_all_well.sh`'s `REQUIRED=( … )` array, in order."""
    lines = _BUILD_SCRIPT.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("REQUIRED=("))
    except StopIteration:
        raise AssertionError(
            f"no `REQUIRED=(` array in {_BUILD_SCRIPT.name} — the script's "
            "preflight roster moved or was renamed, and this guard is now "
            "measuring nothing") from None
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith(")"):
            return out
        m = _REQUIRED_ENTRY.match(ln)
        if m:
            out.append(m.group(1))
    raise AssertionError(
        f"the `REQUIRED=(` array in {_BUILD_SCRIPT.name} is not closed by a "
        "line starting `)` — the parse ran off the end of the file")


def test_no_loose_module_at_the_repository_root():
    """A root `.py` that is not an entry point or repository metadata."""
    unlisted = sorted(_root_py_files() - set(ROOT_MODULES))
    assert not unlisted, (
        "these modules sit at the repository root without being entry points "
        "or repository metadata:\n  " + "\n  ".join(unlisted) + "\n\n"
        "Move each into the package that owns it — a pane's shell belongs "
        "beside the pane's features (`datasets/tab.py`), design tokens and "
        "cross-pane maps belong in `ui/`, launch-time modules in `startup/`, "
        "Qt-free general helpers in `util/`, analysis-domain modules in "
        "`pipeline/` — or, if it really is something the user or the "
        "application *runs*, add it to ROOT_MODULES above with the reason. "
        "Nothing breaks either way, which is exactly why this is a test: the "
        "cost of a loose module is paid entirely by the next person looking "
        "for it.")


def test_the_roster_names_nothing_that_has_gone():
    """The mirror image: an entry for a file that is no longer there.

    A stale entry is not inert. It is a standing permission under a name that
    has left, so the next module to arrive at the root can match it and pass —
    and the roster stops being readable as a description of what is there.
    """
    stale = sorted(name for name in ROOT_MODULES
                   if not (REPO_ROOT / name).is_file())
    assert not stale, (
        "ROOT_MODULES names files that do not exist at the repository root, so "
        f"these entries permit nothing and describe nothing: {', '.join(stale)}."
        " Delete each one.")


def test_the_build_script_requires_only_files_that_exist():
    """`build_all_well.sh`'s preflight roster, held against the tree.

    The script exits 1 on the first missing entry, before PyInstaller is
    invoked, so a stale path does not degrade the build — it prevents it. And
    because nothing in this suite runs the script, the only way that surfaces is
    someone trying to cut a release. It required `analyze_tab.py` long after the
    Analyze pane became `analyze/tab.py`.
    """
    required = _build_script_required()
    assert required, (
        f"parsed no entries out of {_BUILD_SCRIPT.name}'s `REQUIRED=(` array — "
        "the format changed and this guard would now pass whatever the script "
        "says")
    missing = sorted(p for p in required if not (REPO_ROOT / p).exists())
    assert not missing, (
        f"{_BUILD_SCRIPT.name} refuses to build unless these exist, and they "
        "do not:\n  " + "\n  ".join(missing) + "\n\n"
        "Either the path moved and the roster did not follow, or the entry is "
        "obsolete and should go. The script aborts on the first one, so a "
        "release cannot be cut until this list matches the tree.")
