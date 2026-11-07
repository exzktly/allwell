"""Every recursive delete in the app goes through the delete layer, or is a named exemption with its reason.

A user's remote home folder was emptied by an ``rm -rf`` the app built. The delete layer is the one place a recursive
delete is allowed to be spelled: ``services.remote_delete`` on a remote host (``remote_delete``, ``aw_rm``, ``exec_sh``)
and ``util.safe_delete.safe_rmtree`` on this machine. Both refuse anything not strictly inside the directory the delete
is meant for. A delete spelled anywhere else is invisible to them, so this guard finds every one:

* **shell** -- a string the app sends to a shell that holds ``rm -r…``/``rm -…r…``, ``-exec rm`` or ``find … -delete``;
* **argv** -- a list literal ``["rm", "-rf", …]`` or a ``find`` argv with ``-delete`` / ``-exec rm``;
* **rmtree** -- a call to ``shutil.rmtree`` (however it was imported).

An ``["rm", …]`` argv handed straight to a cache's ``._ssh_run(...)`` or to ``remote_batch_ledger.checked(...)`` is
through the layer: ``DatasetCache._ssh_run`` sends every ``rm`` as ``aw_rm`` under the staging directory
(``tests/test_remote_delete_layer.py::test_every_rm_the_cache_sends_goes_through_aw_rm``).

Everything else is an exemption keyed on the **site** -- file and enclosing function or constant -- never on a file,
with the reason written here and at the site. An exemption that no longer matches a delete fails too, so the roster
cannot outlive what it describes. Docstrings and comments are not read: they describe deletes, they do not perform them.

Python sources only (``tests/_repo_scan``). The shell scripts under ``scripts/`` (``build_executable.sh``'s ``build
dist``, ``test_slow.sh``'s report directory, ``prove_guard.sh``'s scratch worktree) are developer tools that run in a
checkout of this repository and delete what they made there; the app never runs them.
"""

from __future__ import annotations

import ast
import re

from tests._repo_scan import parsed_sources, rel

#: The layer itself: the only files allowed to spell a recursive delete without an exemption.
LAYER = {"services/remote_delete.py", "util/safe_delete.py"}

_RM_SHELL = re.compile(r"(?<![\w./-])rm\s+(?:-[A-Za-z-]*\s+)*-[A-Za-z]*[rR]|-exec\s+rm\b|(?<![\w-])find\b[^\n]*\s-delete\b")

#: (file, site) -> why this recursive delete is not through the layer. ``site`` is the enclosing function's qualified
#: name, or the module-level name a string is assigned to. Keep each reason to what makes the site safe without it.
EXEMPT: dict[tuple[str, str], str] = {
    # ── remote ────────────────────────────────────────────────────────────────────────────────────────────────────
    ("services/remote_commands.py", "_batch_driver_snippet"):
        "the node-side driver's `rm -rf --one-file-system -- in` of the run's own input directory, a fixed name under "
        "the run directory the driver is already in; embedding aw_rm would push the launch command past the 8,000-"
        "character cap (8,268) that keeps it from being cut in transit",
    ("services/remote_storage.py", "delete_script"):
        "the Remote storage dialog's Delete… and Auto-delete: its own checks on the host (inside the staging root, a "
        "run or cache the app made, owned by the user, not a mount point, one filesystem, every path resolved with "
        "pwd -P), with each folder named by the person or by the run that made it",
    ("services/remote_bootstrap.py", "_MINIFORGE_REMOVAL"):
        "removes ~/miniforge3 only when it holds the marker file the bootstrap wrote into the miniforge it installed; "
        "anyone's own conda is left and the install stops (tests/test_deletes_keep_to_what_the_app_made.py F1)",
    ("services/remote_bootstrap.py", "force_clean_script"):
        "--force removes the conda env prefix only when it ends in /envs/allwell (or allwell-cellpose), and "
        "~/allwell/code only when it is not a link; ~/allwell as a whole and ~/miniforge3 are left (F2)",
    ("services/remote_bootstrap.py", "env_setup_script"):
        "an echo telling the person what to run by hand on a half-created env; it removes nothing",
    ("services/remote_conda.py", "_conda_env_guard"):
        "an echo telling the person what to run by hand on a half-created env; it removes nothing",
    ("services/remote_batch_ledger.py", "handshake_clear_argv"):
        "`find <run_dir> -maxdepth 1 -type f -name .aw_batch_* -delete`: only regular files directly inside the run "
        "directory, so no truncation of the command can make it reach a directory",
    # ── local ─────────────────────────────────────────────────────────────────────────────────────────────────────
    ("core/local_storage.py", "_remove"):
        "the title bar's storage dialog: removes only entries it listed under the app's own folders, listed again "
        "when Clear is pressed, in-use ones skipped, and a link or junction unlinked where it stands, never followed",
    ("datasets/core/containers.py", "publish_container"):
        "its own `__aw_tmp_<name>.pid<N>` staging beside the destination, and a destination only once a whole copy "
        "of the source is staged to replace it (Datasets tools' outputs; clear_destination's overlap refusal guards "
        "the destination root)",
    ("pipeline/io.py", "remove_directory"):
        "refuses anything not named `_tmp_*` and any link: only the extraction and checkpoint folders the pipeline "
        "names itself (tests/test_deletes_keep_to_what_the_app_made.py)",
    ("pipeline/stardist_cache.py", "_stardist_download_lock"):
        "the fixed-name lock directory this function made with os.mkdir; shutil.rmtree refuses a link there",
    ("review/data/cell_pool.py", "_release"):
        "the spill directory this pool made with tempfile.mkdtemp, removed by the pool's own finalizer",
    ("review/features/batch_export/_destinations.py", "DestinationSpool.discard"):
        "the `.batch_dest_tmp_*` directory this spool made with tempfile.mkdtemp",
    ("review/features/batch_export/service.py", "commit_staged_export"):
        "the `.batch_export_tmp_*` directory make_staging made with tempfile.mkdtemp, emptied by the commit above it",
    ("review/features/batch_export/service.py", "discard_staging"):
        "the `.batch_export_tmp_*` directory make_staging made with tempfile.mkdtemp, on failure or abort",
    ("scripts/derive_profile.py", "_rm"):
        "a developer tool deriving a feature-subset checkout: removes the feature's own paths from the repository "
        "copy it was pointed at, never run by the app",
    ("services/acquisition_transport.py", "_carried_files"):
        "the `allwell-acquisition-*` transit directory this function made with _new_transit_dir (mkdtemp)",
    ("services/remote_fetch.py", "Fetcher._materialize_incremental"):
        "the `allwell-merge-*` extraction directory this function made with _new_transit_dir (mkdtemp) for one well",
    ("services/remote_fetch_place.py", "discard_transit_dir"):
        "the per-well CSV transit directory _new_transit_dir (mkdtemp) made; holds only what the fetch put there",
    ("services/remote_fetch_place.py", "merge_fetched_csv_dir"):
        "the per-well CSV transit directory _new_transit_dir (mkdtemp) made, once every partial in it has merged",
    ("services/remote_scratch.py", "sweep_stale_scratch"):
        "only `allwell-*` entries older than the cutoff directly in the app's scratch and transit folders",
    ("services/remote_staging.py", "_stage_plans"):
        "the staging directory _open_staging_if_needed made with mkdtemp; it holds rewritten copies, never originals",
    ("startup/mpl_bootstrap.py", "_import_reads_a_placeholder"):
        "the `allwell-mplfonts-*` directory this function made with tempfile.mkdtemp",
    ("util/zip_write.py", "replace_folder_with_archive"):
        "removes the folder only when every file in it is one the caller archived (nothing left over); "
        "tests/test_no_unarchived_delete.py holds who may call it",
}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for stmt in body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    ids.add(id(stmt.value))
    return ids


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
    return None


def _is_rm_argv(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return False
    words = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in node.elts]
    head = (words[0] or "").rsplit("/", 1)[-1]
    if head == "rm":
        return any(w and w.startswith("-") and not w.startswith("--") and "r" in w.lower() for w in words[1:])
    if head == "find":
        return "-delete" in words or any(a == "-exec" and b == "rm" for a, b in zip(words, words[1:]))
    return False


def _routed(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "_ssh_run") or (isinstance(f, ast.Name) and f.id == "checked")


def violations(tree: ast.Module, path: str) -> list[tuple[str, str, str, int]]:
    """``(path, site, kind, line)`` for every recursive delete in *tree* not spelled through the layer."""
    if path in LAYER:
        return []
    docs = _docstring_ids(tree)
    rmtree_names = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "shutil"
                    for a in n.names if a.name == "rmtree"}
    found: list[tuple[str, str, str, int]] = []

    def visit(node: ast.AST, site: str, routed: set[int]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            site = node.name if site == "<module>" else f"{site}.{node.name}"
        elif isinstance(node, ast.Assign) and site == "<module>" and isinstance(node.targets[0], ast.Name):
            site = node.targets[0].id
        if isinstance(node, ast.Call):
            if _routed(node):
                routed = routed | {id(a) for a in node.args}
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "rmtree") or (isinstance(f, ast.Name) and f.id in rmtree_names):
                found.append((path, site, "rmtree", node.lineno))
        if _is_rm_argv(node) and id(node) not in routed:
            found.append((path, site, "argv", node.lineno))
        elif isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and id(node) not in docs):
            text = _text(node)
            if text and _RM_SHELL.search(text):
                found.append((path, site, "shell", node.lineno))
            if isinstance(node, ast.JoinedStr):
                return                                  # its parts are read above, as one string
        for child in ast.iter_child_nodes(node):
            visit(child, site, routed)

    visit(tree, "<module>", set())
    return found


def _all():
    return [v for p, tree in parsed_sources() for v in violations(tree, rel(p))]


def test_no_recursive_delete_outside_the_layer():
    unexplained = sorted({(p, s, k, n) for p, s, k, n in _all() if (p, s) not in EXEMPT})
    assert not unexplained, (
        "a recursive delete outside the delete layer (services/remote_delete.py, util/safe_delete.py). Send it "
        "through remote_delete / aw_rm / exec_sh / safe_rmtree, or add the site to EXEMPT with why it cannot remove "
        "anything but what the app made:\n" + "\n".join(f"  {p}:{n}  {s}  ({k})" for p, s, k, n in unexplained))


def test_every_exemption_still_names_a_delete():
    live = {(p, s) for p, s, _k, _n in _all()}
    stale = sorted(set(EXEMPT) - live)
    assert not stale, f"exemptions that no longer match a recursive delete -- remove them: {stale}"


def test_every_exemption_says_why():
    assert all(len(why) > 40 for why in EXEMPT.values())
