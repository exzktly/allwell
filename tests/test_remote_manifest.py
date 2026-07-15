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
    tree = ast.parse(PIPELINE.read_text(), filename=str(PIPELINE))

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


def test_frozen_bundle_ships_the_remote_manifest():
    """Every manifest file must be bundled into the frozen PyInstaller app.

    A built app resolves the remote-manifest paths under ``_MEIPASS`` (the runner
    uploads them to the SLURM/SSH worker), so each entry has to ship in the
    bundle. ``all_well.spec`` bundles the first-party package dirs whole plus a
    list of top-level scripts / data files; a manifest entry outside those — e.g.
    ``_Docs/requirements-remote.txt`` — must be added to the spec explicitly or a
    built-app remote run aborts with ``missing local file``. (Source runs are
    unaffected — this guards the frozen build specifically.)"""
    from services.remote_runner import _REQUIRED_REMOTE_FILES

    spec = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec").read_text()
    bundled_pkgs = ("well_viewer", "widgets", "ui", "services", "pipeline", "util")
    missing = [
        f for f in _REQUIRED_REMOTE_FILES
        if f.split("/")[0] not in bundled_pkgs          # not in a bundled package
        and f not in spec and Path(f).name not in spec  # nor named in the spec
    ]
    assert not missing, (
        "remote-manifest file(s) not bundled by all_well.spec — a built-app "
        "remote run aborts with 'missing local file'. Add them to the spec's "
        "data list:\n  " + "\n  ".join(missing)
    )
