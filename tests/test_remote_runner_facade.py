"""The remote runner's facade and temporary compatibility-export ratchet.

Historically every extraction from ``services.remote_runner`` re-exported all
of its names so tests and first-party callers did not have to move. That made a
smaller physical file without reducing its effective API. This inventory draws
the boundary before the next extractions:

* ``SUPPORTED_FACADE`` is the deliberately stable application/CLI surface.
* ``PRIVATE_COMPAT_EXPORTS`` is migration debt. It may only shrink; each entry
  must still have an external importer and must be the same object as its real
  owner. Once its last importer moves, the stale-entry test forces its removal.

Do not add a name to the compatibility inventory to make this test pass. Import
it from its owner instead. A genuinely new public facade name requires updating
``services.remote_runner.__all__`` and the supported set here with a reviewed
reason.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from tests._repo_scan import parse_source, python_sources

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "services" / "remote_runner.py"

# Stable even when its spelling is private: the manifest is consumed by the
# bootstrap and build spec, which are packaging entry points rather than
# implementation-level callers.
SUPPORTED_FACADE = {
    "LocalInputMode",
    "OutputFilter",
    "RemoteRunner",
    "TRANSIENT_PREFIX",
    "WELL_XFER_PREFIX",
    "ZippedInput",
    "_REQUIRED_REMOTE_FILES",
    "input_well_containers",
    "repo_root",
    "stage_local_zips",
}

# name -> current defining module. These names are imported only by tests. Some
# are compatibility aliases for an extraction that already happened; the rest
# expose runner internals that later R rows will move. R5 takes this mapping to
# zero. Grouping by owner supplies the reason without repeating it per name.
PRIVATE_COMPAT_EXPORTS = {
    # Tests coupled directly to implementation still owned by remote_runner.
    "DatasetCache": "services.remote_runner",
    "Fetcher": "services.remote_runner",
    "_BREADCRUMB_INTERVAL_S": "services.remote_runner",
    "_BatchedRemoteProcess": "services.remote_runner",
    "_LogToLineStream": "services.remote_runner",
    "_RemoteRun": "services.remote_runner",
    "_StagePlan": "services.remote_runner",
    "_UploadHeartbeat": "services.remote_runner",
    "_auto_threshold_args_from_argv": "services.remote_runner",
    "_classify_fetched_wells": "services.remote_runner",
    "_detect_local_input_mode": "services.remote_runner",
    "_empty_well_blanks": "services.remote_runner",
    "_filter_channels_in_staging": "services.remote_runner",
    "_filter_new_fovs_in_staging": "services.remote_runner",
    "_is_image_member": "services.remote_runner",
    "_log_wells_uploaded": "services.remote_runner",
    "_materialize_staging": "services.remote_runner",
    "_member_matches": "services.remote_runner",
    "_plan_staging": "services.remote_runner",
    "_required_tokens": "services.remote_runner",
    "_run_rsync_streaming": "services.remote_runner",
    "_scheduler_handoff_lines": "services.remote_runner",
    "_well_xfer_line": "services.remote_runner",
    "_well_zip_sizes": "services.remote_runner",
    "refresh_pipeline_info_available_from_outputs": "services.remote_runner",
    "run_client_side_completeness_check": "services.remote_runner",
    # Legacy tests of conda command composition.
    "_CONDA_ACTIVATE_SNIPPET": "services.remote_conda",
    "_conda_env_guard": "services.remote_conda",
    "_conda_env_selector": "services.remote_conda",
    "_conda_run_prefix": "services.remote_conda",
    "_conda_run_prefix_for": "services.remote_conda",
    # Legacy tests of scheduler upload-wait timing.
    "UPLOAD_HEARTBEAT_STALE_S": "services.remote_wait_snippets",
    "UPLOAD_WAIT_TIMEOUT_S": "services.remote_wait_snippets",
    # Legacy tests of the already-extracted transport implementation.
    "_RSYNC_RETRY_CODES": "services.remote_transport",
    "_RSYNC_RETRY_DELAYS": "services.remote_transport",
    "_looks_like_rsync_filename": "services.remote_transport",
    "_remote_join": "services.remote_transport",
    "_remote_quote": "services.remote_transport",
    "_rsync_run_cancellable": "services.remote_transport",
    "_rsync_ssh_transport": "services.remote_transport",
    "_rsync_supports_append_verify": "services.remote_transport",
    "_rsync_supports_info_flag": "services.remote_transport",
    "_rsync_version": "services.remote_transport",
    "_rsync_with_retry": "services.remote_transport",
    # Legacy tests of the extracted batch-command builder.
    "BATCH_DONE": "services.remote_commands",
    "BATCH_DONE_MARKER": "services.remote_commands",
    "BATCH_READY": "services.remote_commands",
    "_batch_driver_snippet": "services.remote_commands",
    "_compose_driver_cmd": "services.remote_commands",
    "_compose_remote_cmd": "services.remote_commands",
    "_remote_scratch_export": "services.remote_commands",
    # Legacy tests of run addressing and reuse identity.
    "UPLOAD_HEARTBEAT": "services.remote_run_id",
    "UPLOAD_SENTINEL": "services.remote_run_id",
    "_choose_remote_output_dir": "services.remote_run_id",
    "_compute_run_id": "services.remote_run_id",
    "pipeline_argv_digest": "services.remote_run_id",
    "remote_run_dir": "services.remote_run_id",
    "remote_run_paths": "services.remote_run_id",
    # Legacy tests of scratch/transit helpers.
    "TRANSIT_SUBDIR": "services.remote_scratch",
    "_scratch_dir": "services.remote_scratch",
    "_transit_dir": "services.remote_scratch",
    "_zip_is_complete": "services.remote_scratch",
    "source_fingerprint": "services.remote_scratch",
    "sweep_stale_scratch": "services.remote_scratch",
    # Tests historically obtained the config type through the runner.
    "RemoteHost": "services.remote_config",
}


def _literal_all() -> set[str]:
    tree = parse_source(RUNNER)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id == "__all__":
                return set(ast.literal_eval(node.value))
    raise AssertionError("services.remote_runner must declare a literal __all__")


def _runner_importers() -> dict[str, set[str]]:
    users: dict[str, set[str]] = {}
    sources = python_sources() + sorted((REPO_ROOT / "tests").rglob("*.py"))
    for path in sources:
        if path == RUNNER:
            continue
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "services.remote_runner":
                continue
            for alias in node.names:
                users.setdefault(alias.name, set()).add(
                    path.relative_to(REPO_ROOT).as_posix())
    return users


def test_remote_runner_supported_facade_is_explicit():
    assert _literal_all() == SUPPORTED_FACADE


def test_private_compatibility_exports_are_live_and_owned_elsewhere():
    """The migration-debt list only shrinks and never masks a second object."""
    runner = importlib.import_module("services.remote_runner")
    users = _runner_importers()
    stale = set(PRIVATE_COMPAT_EXPORTS) - set(users)
    assert not stale, (
        "Private remote_runner compatibility exports have no importers; remove "
        "their aliases and inventory entries:\n  " + "\n  ".join(sorted(stale))
    )

    wrong_owner = []
    for name, module_name in PRIVATE_COMPAT_EXPORTS.items():
        owner = importlib.import_module(module_name)
        if getattr(runner, name) is not getattr(owner, name):
            wrong_owner.append(f"{name}: expected the object from {module_name}")
    assert not wrong_owner, "\n".join(wrong_owner)


def test_every_imported_runner_name_is_supported_or_inventoried():
    users = _runner_importers()
    unexpected = set(users) - SUPPORTED_FACADE - set(PRIVATE_COMPAT_EXPORTS)
    assert not unexpected, (
        "New imports from services.remote_runner expand its effective API. "
        "Import private helpers from their owner instead; review genuinely "
        "public additions explicitly:\n  " + "\n  ".join(sorted(unexpected))
    )
