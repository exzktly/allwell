"""``process_microscopy``'s re-exports are classified, live, and only shrink.

The pipeline orchestrator re-exported most of what moved out of it into
``pipeline/`` so that tests and a few GUI call sites did not have to change.
Row A0 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` measured 45
pure re-exports — names imported and never read by the module itself — with
``tests/_facade_scan.py``, which sees every access form (``from … import``,
attribute reads and writes under any alias, named setattr/patch calls, string
patch targets), and checked every file type for the rest. Twenty-five had no
consumer at all and were removed; one more went when the only test reaching it
turned out to patch an alias nothing read
(``tests/test_no_patch_misses_its_target.py``).

What is left is classified here. A first-party alias is a GUI module importing a
pipeline name through the orchestrator rather than from its owner; a test-only
alias is a test doing the same. Both are migration debt (row A1 takes them to the
owners); a side-effect import is not a re-export at all and stays.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from tests._facade_scan import consumers_of, reexports
from tests._repo_scan import parse_source

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "process_microscopy.py"
MODULE = "process_microscopy"

SIDE_EFFECT = "side-effect import"
FIRST_PARTY = "first-party transitional alias"
TEST_ONLY = "test-only alias"

INVENTORY: dict[str, str] = {
    # Imported for what importing it does, never re-exported as a name.
    "tifffile": SIDE_EFFECT,
    # The Image Browser Playground reaches the pipeline's background code
    # through the orchestrator.
    "BACKGROUND_METHODS": FIRST_PARTY,
    "FLATFIELD_MODES": FIRST_PARTY,
    "estimate_background_field": FIRST_PARTY,
    # Reached only by tests.
    "_FLATFIELD_FLOOR": TEST_ONLY,
    "_FLATFIELD_SIGMA": TEST_ONLY,
    "_POOLED_TP": TEST_ONLY,
    "_bg_well_key": TEST_ONLY,
    "_isotropic_disk_tophat": TEST_ONLY,
    "_load_image_source": TEST_ONLY,
    "_match_background_scale": TEST_ONLY,
    "_normalize_flatfield": TEST_ONLY,
    "_purge_stardist_cache": TEST_ONLY,
    "_require_empty_well_models": TEST_ONLY,
    "_segmentation_tokens": TEST_ONLY,
    "_split_background_wells": TEST_ONLY,
    "_stardist_cache_models_dir": TEST_ONLY,
    "apply_tophat": TEST_ONLY,
    "estimate_basic_flatfield": TEST_ONLY,
    "sample_empty_well_images": TEST_ONLY,
}


def _consumers():
    return consumers_of(MODULE, exclude=[MODULE_PATH])


def test_every_re_export_is_classified():
    pure = reexports(MODULE_PATH)
    unclassified = sorted(pure - set(INVENTORY))
    gone = sorted(set(INVENTORY) - pure)
    assert not unclassified, (
        "process_microscopy re-exports names nobody classified. If nothing "
        "reaches one, delete the import; otherwise import it from its owner:\n  "
        + "\n  ".join(unclassified))
    assert not gone, f"inventoried names that are no longer re-exported: {gone}"


def test_each_alias_is_reached_by_the_kind_of_caller_it_claims():
    users = _consumers()
    wrong = []
    for name, kind in INVENTORY.items():
        if kind == SIDE_EFFECT:
            continue
        files = {f for f, _form in users.get(name, ())}
        app = {f for f in files if not f.startswith("tests/")}
        tests = files - app
        if kind == FIRST_PARTY and not app:
            wrong.append(f"{name}: no first-party caller any more — reclassify or delete")
        if kind == TEST_ONLY and app:
            wrong.append(f"{name}: reached by application code {sorted(app)}")
        if kind == TEST_ONLY and not tests:
            wrong.append(f"{name}: no test reaches it — delete the alias")
    assert not wrong, "\n".join(wrong)


def test_aliases_are_the_owner_s_object():
    module = importlib.import_module(MODULE)
    bound_from = {}
    for node in parse_source(MODULE_PATH).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound_from[alias.asname or alias.name] = (node.module, alias.name)
    wrong = []
    for name, kind in INVENTORY.items():
        if kind == SIDE_EFFECT:
            continue
        owner_mod, original = bound_from[name]
        owner = importlib.import_module(owner_mod)
        if getattr(module, name) is not getattr(owner, original):
            wrong.append(f"{name}: not {owner_mod}.{original}")
    assert not wrong, "\n".join(wrong)
