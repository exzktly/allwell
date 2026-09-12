"""Guardrail: the remote upload manifest covers every repo-root module that
``process_microscopy`` imports.

The remote worker only receives the files listed in
``services.remote_runner._REQUIRED_REMOTE_FILES``. If the pipeline grows a new
repo-root dependency (as happened with ``flatfield_core`` and now
``pipeline.background``) and it's not added to the manifest, the remote run
aborts with ``ModuleNotFoundError`` — a class of bug this test prevents.

It scans *all* imports in ``process_microscopy.py`` (module-level and
function-local), keeps the ones that resolve to a sibling repo-root ``*.py``
module, and asserts each is in the manifest.
"""

import ast
from pathlib import Path
from tests._repo_scan import parse_source

try:  # tomllib is stdlib on 3.11+; fall back to tomli on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / "process_microscopy.py"


def _repo_root_modules() -> set[str]:
    return {p.stem for p in REPO_ROOT.glob("*.py")}


def _imported_top_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Only absolute imports (level 0) name a top-level module.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_remote_manifest_covers_pipeline_root_imports():
    from services.remote_runner import _REQUIRED_REMOTE_FILES

    manifest = set(_REQUIRED_REMOTE_FILES)
    root_modules = _repo_root_modules()
    tree = parse_source(PIPELINE)

    needed = {
        f"{name}.py"
        for name in _imported_top_modules(tree)
        if name in root_modules and name != "process_microscopy"
    }
    missing = sorted(f for f in needed if f not in manifest)
    assert not missing, (
        "process_microscopy imports repo-root module(s) not in the remote "
        "upload manifest (services.remote_runner._REQUIRED_REMOTE_FILES). The "
        "remote run will crash with ModuleNotFoundError. Add them:\n  "
        + "\n  ".join(missing)
    )


def test_manifest_includes_all_pipeline_and_util_files():
    """Every ``.py`` in the ``pipeline/`` and ``util/`` packages must be in the
    manifest. Those packages are synced file-by-file, not as whole directories,
    so a submodule that another *uploaded* module imports — e.g. ``pipeline.io``
    importing ``pipeline.incremental_keys`` — is absent on the remote and crashes
    with ModuleNotFoundError unless it is listed. The top-level-import scan above
    can't see it (it only registers the ``pipeline`` package name), so this pins
    the whole uploaded package."""
    from services.remote_runner import _REQUIRED_REMOTE_FILES

    manifest = set(_REQUIRED_REMOTE_FILES)
    on_disk = {
        str(p.relative_to(REPO_ROOT))
        for pkg in ("pipeline", "util")
        for p in (REPO_ROOT / pkg).glob("*.py")
    }
    missing = sorted(on_disk - manifest)
    assert not missing, (
        "pipeline/ or util/ file(s) not in the remote upload manifest "
        "(services.remote_runner._REQUIRED_REMOTE_FILES) — a remote run that "
        "imports them crashes with ModuleNotFoundError. Add them:\n  "
        + "\n  ".join(missing)
    )


def _spec_data_entries() -> list[tuple[str, str]]:
    """The spec's ``(source, dest)`` data tuples, as repo-relative source paths.

    Parsed from the spec's syntax tree rather than matched as text, because the
    thing that matters here is the *pair*: a source path bundled to the wrong
    destination ships the file somewhere the frozen app does not look.
    """
    import ast

    spec = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec").read_text()
    out: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(spec)):
        if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
            continue
        src, dest = node.elts
        if not isinstance(dest, ast.Constant) or not isinstance(dest.value, str):
            continue
        # `str(_parent / "a" / "b")` → the segments after _parent.
        parts: list[str] = []
        cur = src
        while isinstance(cur, ast.Call) and getattr(cur.func, "id", "") == "str":
            cur = cur.args[0]
        while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            if isinstance(cur.right, ast.Constant):
                parts.insert(0, cur.right.value)
            cur = cur.left
        if isinstance(cur, ast.Name) and cur.id == "_parent" and parts:
            out.append(("/".join(parts), dest.value))
    return out


def test_frozen_bundle_ships_the_remote_manifest():
    """Every manifest file must be bundled, **and at the path the runner reads**.

    A built app resolves the remote-manifest paths under ``_MEIPASS`` (the runner
    uploads them to the SLURM/SSH worker), so each entry has to ship in the
    bundle *at its own relative path*. ``all_well.spec`` bundles the first-party
    package dirs whole plus a list of explicit ``(source, dest)`` data tuples; a
    manifest entry outside a bundled package — ``requirements-remote.txt``
    is the only one today — must appear as such a tuple whose destination equals
    the entry's own parent directory.

    **The destination is the half that fails silently**, which is why this
    checks the pair. The previous version asked only whether the file's
    *basename* appeared anywhere in the spec text. Move the file and update the
    manifest and the spec's source path while leaving the old destination, and
    PyInstaller builds cleanly, this guard passes on the basename, and the only
    symptom is a frozen-app remote run aborting with ``missing local file`` —
    in a build the spec's own comments record as exercised by no test at all.

    The bundled-package list is read out of the spec rather than restated here.
    A second roster of the same thing is how a package added to one and not the
    other goes unnoticed, and this file is not the place that list lives.
    """
    from services.remote_runner import _REQUIRED_REMOTE_FILES

    entries = _spec_data_entries()
    assert entries, (
        "parsed no (source, dest) data tuples out of all_well.spec — its "
        "format changed and this guard would now pass whatever the spec says")

    spec_text = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec").read_text()
    bundled_pkgs = _spec_bundled_packages(spec_text)
    by_source = dict(entries)

    missing, misplaced = [], []
    for f in _REQUIRED_REMOTE_FILES:
        if f.split("/")[0] in bundled_pkgs:
            continue
        if f not in by_source:
            missing.append(f)
            continue
        want = str(Path(f).parent) if "/" in f else "."
        got = by_source[f]
        if got != want:
            misplaced.append(f"{f} → bundled to {got!r}, must be {want!r}")

    assert not missing, (
        "remote-manifest file(s) not bundled by all_well.spec — a built-app "
        "remote run aborts with 'missing local file'. Add them to the spec's "
        "data list:\n  " + "\n  ".join(missing)
    )
    assert not misplaced, (
        "remote-manifest file(s) bundled to the wrong destination. The frozen "
        "app looks for each at _MEIPASS/<its own relative path>, so the "
        "destination must equal the parent directory of the manifest string:\n  "
        + "\n  ".join(misplaced) + "\n\n"
        "This is what happens when the file moves and only one of the two "
        "halves follows. The build succeeds and only a remote run from the "
        "shipped app notices."
    )


def _spec_bundled_packages(spec_text: str) -> set[str]:
    """The package names the spec's `for _pkg in (...)` walk bundles whole."""
    import ast

    for node in ast.walk(ast.parse(spec_text)):
        if (isinstance(node, ast.For) and getattr(node.target, "id", "") == "_pkg"
                and isinstance(node.iter, ast.Tuple)):
            return {e.value for e in node.iter.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    raise AssertionError(
        "no `for _pkg in (...)` package walk found in all_well.spec — the "
        "spec's bundling changed shape and this guard is measuring nothing")


def test_no_profile_bundle_drops_a_remote_manifest_file():
    """No feature profile may drop a remote-manifest file from the frozen bundle.

    ``all_well.spec`` bundles first-party packages per-file under a profile,
    filtering out each excluded feature's ``repo_paths``. The Cellpose feature
    owns ``pipeline/cellpose.py`` — which is ALSO in ``_REQUIRED_REMOTE_FILES`` —
    so a Cellpose-excluded profile would drop it from the ``.app`` and every
    remote run would abort at ``RemoteRunner.sync_required_files``' local-existence
    check ("missing local file"), even for StarDist-only users.

    The spec defends against this with a bundling FLOOR: it subtracts
    ``_REQUIRED_REMOTE_FILES`` from the exclusion list. This test replicates that
    floor for EVERY profile using the same shared match rule (``path_is_excluded``)
    the spec's ``_pkg_datas`` uses, and asserts no manifest file is dropped — so a
    future profile (e.g. Analyze-with-no-Cellpose) can't silently reintroduce the
    bug. ``test_spec_applies_the_remote_manifest_bundling_floor`` binds the floor
    to the spec itself."""
    from services.remote_runner import _REQUIRED_REMOTE_FILES
    from scripts.profile_build import (
        compute_exclusions, load_catalog, path_is_excluded,
    )

    profiles_dir = REPO_ROOT / "profiles"
    catalog = load_catalog(profiles_dir / "full.toml")
    manifest = list(_REQUIRED_REMOTE_FILES)
    floor = set(manifest)

    offenders: list[str] = []
    for toml in sorted(profiles_dir.glob("*.toml")):
        if toml.stem == "full":
            continue
        prof = tomllib.loads(toml.read_text(encoding="utf-8"))
        ex = compute_exclusions(
            catalog, prof.get("features"), prof.get("subtabs") or {},
        )
        # Apply the bundling floor exactly as all_well.spec does.
        excluded = [d for d in ex.repo_paths if d not in floor]
        offenders += [
            f"{toml.stem}: {f}" for f in manifest if path_is_excluded(f, excluded)
        ]
    assert not offenders, (
        "profile(s) would drop a remote-manifest file from the frozen bundle "
        "even with the floor — a built-app remote run under that profile aborts "
        "with 'missing local file':\n  " + "\n  ".join(offenders)
    )


def test_spec_applies_the_remote_manifest_bundling_floor():
    """The spec must actually subtract the remote manifest from the profile
    exclusion list — otherwise the floor the test above relies on isn't real."""
    spec = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec").read_text()
    assert "from services.remote_runner import _REQUIRED_REMOTE_FILES" in spec
    assert "d not in _floor" in spec, (
        "all_well.spec no longer subtracts the remote manifest from "
        "_excluded_repo_dirs — a Cellpose-excluded profile will drop "
        "pipeline/cellpose.py and break remote runs."
    )


def test_bootstrap_preflight_uses_the_runner_manifest():
    """The bootstrap's local preflight/log must read the SAME file list the
    runner uploads, so the two can never drift (the old ``REQUIRED_FILES`` tuple
    silently rotted to a stale 4-of-14 subset)."""
    from services.remote_runner import _REQUIRED_REMOTE_FILES
    from services.remote_bootstrap import _required_files

    assert tuple(_required_files()) == tuple(_REQUIRED_REMOTE_FILES)
