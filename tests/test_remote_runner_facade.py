"""The remote runner's facade, and the inventory of everything else reached
through it.

``services/remote_runner.py`` grew by extraction: each time a cluster moved out
(transport, conda, run ids, scratch, wait snippets, suspend), the runner kept
re-exporting the moved names so its tests did not have to change. That made a
smaller file without making a smaller API, because every test still reaches the
old module and the module graph behind it is invisible to callers.

This guard draws the boundary before the next extractions
(`_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`, row R0):

* ``SUPPORTED_FACADE`` is the stable application/CLI surface and equals the
  module's ``__all__``.
* ``COMPAT_INVENTORY`` is every *other* name some file reaches through
  ``services.remote_runner``, classified by what it is. It is migration debt and
  only shrinks; workstream R takes it to zero.

"Reaches" means any of four access forms, not only ``from … import``: module
attribute reads and writes (``rr.X``), named setattr/patch calls
(``monkeypatch.setattr(rr, "X", …)``), and string patch targets
(``"services.remote_runner.X"``). An import-only scan once reported eleven
re-exports as unused while ``tests/test_remote_transit_volume.py`` still
cleared ``rr._transit_refused`` through the module. ``tests/_facade_scan.py``
holds the scan, and ``tests/test_guards_bite.py`` shows it seeing each form.

Do not add a name to the inventory to make this test pass; import it from its
owner instead. A genuinely new public facade name updates ``__all__`` and
``SUPPORTED_FACADE`` together, with a reviewed reason.
"""

from __future__ import annotations

import ast
import functools
import importlib
from pathlib import Path

from tests._facade_scan import consumers_of, module_level_names, reexports
from tests._repo_scan import parse_source

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "services" / "remote_runner.py"
MODULE = "services.remote_runner"

#: Stable even where the spelling is private: ``_REQUIRED_REMOTE_FILES`` is the
#: packaging contract the bootstrap and the PyInstaller spec read by this path.
#: ``LocalInputMode`` and ``ZippedInput`` are the staging request types the CLI
#: and ``PipelineRunner`` hand to ``stage_local_zips``.
SUPPORTED_FACADE = frozenset({
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
})

#: A pure re-export: imported from its owner and never read by the runner
#: itself. Consumers should import it from the owner; the alias then goes.
ALIAS = "alias"
#: Imported from its owner *and* used by the runner. Consumers that patch it
#: through the runner are patching the runner's own binding — a real seam
#: today, which moves to the owning module when the code that reads it does.
BINDING = "binding"
#: Defined in the runner and reached by tests. Workstream R moves each one to
#: the module that owns its subject; the tests then import it from there.
OWNED = "owned"

COMPAT_INVENTORY: dict[str, str] = {
    # Pure re-exports of an extracted owner.
    "BATCH_DONE_MARKER": ALIAS,
    "_CONDA_ACTIVATE_SNIPPET": ALIAS,
    "_conda_env_guard": ALIAS,
    "_conda_run_prefix": ALIAS,
    "_remote_scratch_export": ALIAS,
    "_rsync_run_cancellable": ALIAS,
    "_rsync_version": ALIAS,
    "_scratch_dir": ALIAS,
    "_transit_refused": ALIAS,
    "TRANSIT_SUBDIR": ALIAS,
    "UPLOAD_HEARTBEAT": ALIAS,
    "UPLOAD_SENTINEL": ALIAS,
    "UPLOAD_WAIT_TIMEOUT_S": ALIAS,
    # The runner's own binding of an imported name, read or patched by tests.
    "BATCH_DONE": BINDING,
    "_batch_driver_snippet": BINDING,
    "BATCH_READY": BINDING,
    "_choose_remote_output_dir": BINDING,
    "_compose_driver_cmd": BINDING,
    "_compose_remote_cmd": BINDING,
    "_compute_run_id": BINDING,
    "_conda_env_selector": BINDING,
    "_conda_run_prefix_for": BINDING,
    "_forced_from_argv": BINDING,
    "_local_input_dir_from_argv": BINDING,
    "_looks_like_rsync_filename": BINDING,
    "os": BINDING,
    "pipeline_argv_digest": BINDING,
    "_pipeline_launch_is_pointless": BINDING,
    "_remote_join": BINDING,
    "_remote_quote": BINDING,
    "remote_run_dir": BINDING,
    "remote_run_paths": BINDING,
    "RemoteHost": BINDING,
    "_RSYNC_RETRY_CODES": BINDING,
    "_RSYNC_RETRY_DELAYS": BINDING,
    "_rsync_ssh_transport": BINDING,
    "_rsync_supports_append_verify": BINDING,
    "_rsync_supports_info_flag": BINDING,
    "_rsync_with_retry": BINDING,
    "source_fingerprint": BINDING,
    "subprocess": BINDING,
    "sys": BINDING,
    "time": BINDING,
    "UPLOAD_HEARTBEAT_STALE_S": BINDING,
    # Defined in the runner; tests reach its internals.
    "_auto_threshold_args_from_argv": OWNED,
    "_BatchedRemoteProcess": OWNED,
    "DatasetCache": OWNED,
    "_empty_well_blanks": OWNED,
    "_log_wells_uploaded": OWNED,
    "_LogToLineStream": OWNED,
    "_run_rsync_streaming": OWNED,
    "_scheduler_handoff_lines": OWNED,
    "_stream_rsync_with_retry": OWNED,
    "_UploadHeartbeat": OWNED,
    "_well_zip_sizes": OWNED,
}


@functools.cache
def _consumers() -> dict[str, set[tuple[str, str]]]:
    return consumers_of(MODULE, exclude=[RUNNER])


def _literal_all() -> set[str]:
    runner = importlib.import_module(MODULE)
    names = getattr(runner, "__all__", None)
    assert names is not None, f"{MODULE} must declare __all__"
    return set(names)


def _actual_kind(name: str, bound: dict[str, str], pure: set[str]) -> str:
    if name in pure:
        return ALIAS
    return OWNED if bound.get(name) == "defined" else BINDING


def test_remote_runner_supported_facade_is_explicit():
    assert _literal_all() == set(SUPPORTED_FACADE)


def test_every_name_reached_through_the_runner_is_supported_or_inventoried():
    users = _consumers()
    unexpected = set(users) - SUPPORTED_FACADE - set(COMPAT_INVENTORY)
    assert not unexpected, (
        "New reaches into services.remote_runner widen its effective API. "
        "Import from the owning module instead:\n  " + "\n  ".join(
            f"{n}: {sorted(users[n])}" for n in sorted(unexpected))
    )


def test_inventory_entries_are_still_reached():
    """The inventory only shrinks: an entry nobody reaches must be deleted."""
    stale = set(COMPAT_INVENTORY) - set(_consumers())
    assert not stale, (
        "No file reaches these through services.remote_runner any more; "
        "delete them from COMPAT_INVENTORY (and delete the alias if it is "
        "one):\n  " + "\n  ".join(sorted(stale))
    )


def test_inventory_kinds_match_the_tree():
    """A move changes what a name is; the inventory has to say so."""
    bound = module_level_names(RUNNER)
    pure = reexports(RUNNER)
    wrong = {n: (k, _actual_kind(n, bound, pure))
             for n, k in COMPAT_INVENTORY.items()
             if n in bound and _actual_kind(n, bound, pure) != k}
    missing = sorted(n for n in COMPAT_INVENTORY if n not in bound)
    assert not missing, f"Inventoried names the runner no longer binds: {missing}"
    assert not wrong, "\n".join(
        f"{n}: declared {d}, actually {a}" for n, (d, a) in sorted(wrong.items()))


def test_the_runner_carries_no_dead_reexport():
    """A pure re-export nobody reaches is dead weight in the facade."""
    dead = reexports(RUNNER) - set(_consumers()) - SUPPORTED_FACADE
    assert not dead, (
        "services.remote_runner re-exports names no file reaches through it; "
        "delete the imports:\n  " + "\n  ".join(sorted(dead))
    )


def test_aliases_are_the_owner_s_object():
    """An alias must be the owner's object, never a copy that can diverge."""
    runner = importlib.import_module(MODULE)
    bound_from = {}
    for node in parse_source(RUNNER).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound_from[alias.asname or alias.name] = (node.module, alias.name)
    wrong = []
    for name, kind in COMPAT_INVENTORY.items():
        if kind != ALIAS:
            continue
        module, original = bound_from[name]
        owner = importlib.import_module(module)
        if getattr(runner, name) is not getattr(owner, original):
            wrong.append(f"{name}: not the object {module}.{original}")
    assert not wrong, "\n".join(wrong)
