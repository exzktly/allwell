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
        "and the `--run-pipeline` / `--run-zipper` / `--run-bootstrap` "
        "subprocess dispatcher. That dispatch is how a helper which is *not* a "
        "root module still gets run inside a frozen bundle, where "
        "`sys.executable` is the .app binary rather than an interpreter",
    "version.py":
        "repository metadata — the one version literal, parsed by path from "
        "outside Python by `scripts/preflight.sh` and the PyInstaller spec, so "
        "it cannot move into a package without both learning where it went",
}

#: Directories allowed at the repository root, with what each is.
#:
#: The rule above is stated for *three* kinds of thing and only one of them was
#: guarded: a new loose root directory passed silently while a new loose root
#: module failed. `design/` — two HTML mockups occupying a root directory until
#: this rule existed — is exactly the shape that would have drifted back.
#:
#: A package is a directory holding application code. The rest are here because
#: something outside Python reads them at a fixed path.
ROOT_DIRS = {
    "analyze": "package — the Analyze pane",
    "core": "package — cross-cutting app infrastructure no pane owns",
    "datasets": "package — the Datasets pane and its tools",
    "pipeline": "package — the Qt-free analysis pipeline",
    "services": "package — the Analyze-side service layer",
    "startup": "package — what runs before the application exists",
    "ui": "package — app-level UI every pane shares",
    "util": "package — Qt-free general helpers",
    "well_viewer": "package — the Review pane",
    "widgets": "package — reusable Qt widgets",
    "scripts": "dev tooling + `_bootstrap_lib.py`, which ships in the bundle",
    "tests": "the pytest suite",
    "assets":
        "runtime resources read by path — `ui.theme._icon_path` and "
        "`startup.mpl_bootstrap` resolve `assets/icons` and `assets/fonts` "
        "against the repository root, and the spec bundles them at that layout",
    "profiles":
        "build profiles, read by name from `ALLWELL_PROFILE` at build and run "
        "time (`scripts/profile_build.py`)",
    "_Docs":
        "everything that is not source — the live docs, the installer and its "
        "spec, the requirements lists, the design mockups, the visual audit. "
        "`_Docs/requirements-remote.txt` is uploaded to a remote worker by that "
        "exact string, so this name is load-bearing",
}

#: Directories that are not content: version control, tooling caches, build
#: output, and virtualenvs. Matched by name or by leading dot.
_DIR_IGNORE = {
    ".git", "__pycache__", "dist", "build", "node_modules", "venv", ".venv",
}


#: The build script's own preflight roster, and where to find it.
_BUILD_SCRIPT = REPO_ROOT / "_Docs" / "_Installation" / "build_all_well.sh"

#: `    "some/path.py"` inside the `REQUIRED=( … )` array, with an optional
#: trailing comment.
#:
#: The comment half is not decoration. `    "analyze_tab.py"   # stale` is valid
#: bash, is enforced by the build script exactly like a bare entry, and the first
#: version of this pattern could not see it — so the guard passed while the build
#: still refused to start. The array on `origin/main` already carried comment
#: lines, so this is the repository's real formatting, not a hypothetical.
_REQUIRED_ENTRY = re.compile(r'^\s*"([^"]+)"\s*(?:#.*)?$')

#: A line inside the array that is neither an entry, a comment, nor blank. The
#: parser skipping such a line silently is the other way this check fails open.
_REQUIRED_IGNORABLE = re.compile(r"^\s*(?:#.*)?$")


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
    out, unparsed = [], []
    for ln in lines[start + 1:]:
        if ln.startswith(")"):
            assert not unparsed, (
                f"lines inside {_BUILD_SCRIPT.name}'s `REQUIRED=(` array that this "
                f"parser does not understand, so it is silently reading fewer "
                f"requirements than the script enforces: {unparsed}")
            return out
        m = _REQUIRED_ENTRY.match(ln)
        if m:
            out.append(m.group(1))
        elif not _REQUIRED_IGNORABLE.match(ln):
            unparsed.append(ln.strip())
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


def _root_dirs() -> set[str]:
    """Every directory at the repository root that is content rather than debris."""
    return {d.name for d in REPO_ROOT.iterdir()
            if d.is_dir() and not d.name.startswith(".")
            and d.name not in _DIR_IGNORE}


def test_no_loose_directory_at_the_repository_root():
    """A root directory that is not a package or a named fixed-path resource.

    The half of the rule that had no test. It is the easier half to drift, too:
    adding a directory feels more deliberate than adding a file and is therefore
    less likely to be questioned, and a directory holding two files reads as
    permanent structure the moment it exists.
    """
    unlisted = sorted(_root_dirs() - set(ROOT_DIRS))
    assert not unlisted, (
        "these directories sit at the repository root without being packages or "
        "declared fixed-path resources:\n  " + "\n  ".join(unlisted) + "\n\n"
        "A package of application code belongs in ROOT_DIRS above (and in "
        "`tests/_repo_scan.PACKAGE_ROOTS`, or every AST guard is blind to it). "
        "Documentation, mockups, screenshots and one-off material belong under "
        "`_Docs/`. If it really must sit at the root because something outside "
        "Python reads it there, add it above with that reason written down.")


def test_the_directory_roster_names_nothing_that_has_gone():
    """The mirror image, for the same reason as the module roster."""
    stale = sorted(name for name in ROOT_DIRS
                   if not (REPO_ROOT / name).is_dir())
    assert not stale, (
        "ROOT_DIRS names directories that do not exist at the repository root, "
        f"so these entries permit nothing and describe nothing: {', '.join(stale)}."
        " Delete each one.")


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
