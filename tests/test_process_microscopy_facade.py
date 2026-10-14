"""``process_microscopy`` re-exports nothing, and nobody reaches through it for
what it imports.

The pipeline orchestrator used to re-export most of what moved out of it into
``pipeline/`` so that tests and a few GUI call sites did not have to change.
Row A0 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) measured 45
pure re-exports with ``tests/_facade_scan.py`` — which sees every access form:
``from … import``, attribute reads and writes under any alias, named
setattr/patch calls and string patch targets — and removed the 26 nothing
reached. Row A1 moved every remaining caller to the module that owns the name
and removed the rest, along with a PEP 562 ``__getattr__`` that re-exported
three Cellpose names for two tests.

What this holds now:

* the module's only import that is not used by the module is a side-effect
  import;
* no application code reaches, through ``process_microscopy``, a name the
  module merely imports — it imports it from the owner;
* a test reaches such a name only to patch a binding the orchestrator itself
  calls, and never reads one through it. Patching the owner would not reach that call, because the
  orchestrator holds its own binding of the name; each seam is listed with the
  test that needs it, and an entry nobody reaches any more fails.

Names the orchestrator *defines* are its own and are not policed here.
"""

from __future__ import annotations

import functools
from pathlib import Path

from tests._facade_scan import PATCH_FORMS, consumers_of, module_level_names, reexports

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "process_microscopy.py"
MODULE = "process_microscopy"

#: Imported for what importing it does, never re-exported as a name.
SIDE_EFFECT_IMPORTS = {
    "tifffile": "registers the TIFF codecs the image readers rely on",
}

#: Imported names the orchestrator calls, patched on it by tests that steer
#: that call.
BINDING_SEAMS = {
    "ProcessPoolExecutor": "tests/test_well_worker_pool.py runs the pool in-process",
    "as_completed": "tests/test_well_worker_pool.py, paired with the executor",
    "preflight_scratch_for_run": "tests/test_capacity.py stubs the scratch preflight",
    "prepare_models": "tests/test_only_wells_run.py wraps the model build",
    "plan_parallelism": "tests/test_acquisition_remote_transport.py stops main() "
                        "at the first step after the acquisition-record gate",
}


@functools.cache
def _consumers():
    return consumers_of(MODULE, exclude=[MODULE_PATH])


def _imported_names_reached() -> dict[str, set[str]]:
    """``name -> files`` for every name the module imports (rather than
    defines) that some other file reaches through it."""
    bound = module_level_names(MODULE_PATH)
    return {name: {f for f, _form in users}
            for name, users in _consumers().items()
            if name in bound and bound[name] != "defined"}


def test_the_only_unused_import_is_a_side_effect_import():
    extra = sorted(reexports(MODULE_PATH) - set(SIDE_EFFECT_IMPORTS))
    assert not extra, (
        "process_microscopy imports names it does not use. If something reaches "
        "one through it, import it from the owner instead; then delete the "
        "import:\n  " + "\n  ".join(extra))


def test_no_application_code_reaches_an_imported_name_through_it():
    wrong = {name: sorted(f for f in files if not f.startswith("tests/"))
             for name, files in _imported_names_reached().items()}
    wrong = {n: f for n, f in wrong.items() if f}
    assert not wrong, (
        "Import these from the module that defines them, not through "
        "process_microscopy:\n  " + "\n  ".join(f"{n}: {f}" for n, f in sorted(wrong.items())))


def test_tests_reach_an_imported_name_only_to_patch_a_binding():
    reached = _imported_names_reached()
    unexpected = sorted(set(reached) - set(BINDING_SEAMS))
    stale = sorted(set(BINDING_SEAMS) - set(reached))
    assert not unexpected, (
        "Tests reach these imported names through process_microscopy. Read them "
        "from their owner; only a patch of a binding the orchestrator calls "
        "belongs here, listed in BINDING_SEAMS with its reason:\n  "
        + "\n  ".join(f"{n}: {sorted(reached[n])}" for n in unexpected))
    assert not stale, f"BINDING_SEAMS entries nobody reaches any more: {stale}"


def test_a_binding_seam_is_only_ever_patched_through_it():
    """A seam is listed because patching the orchestrator's binding is the one
    way to steer the call it makes. Reading one through the orchestrator is a
    read of a name it only imports, which belongs on the owner."""
    reads = sorted(f"{name}: {shown} ({form})"
                   for name, users in _consumers().items() if name in BINDING_SEAMS
                   for shown, form in users if form not in PATCH_FORMS)
    assert not reads, (
        "These read a binding seam through process_microscopy; read it from the "
        "module that defines it and keep only the patch here:\n  "
        + "\n  ".join(reads))
