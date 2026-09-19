"""Every pipeline-declared sidecar block is on the plate-merge roster.

``datasets.merge_plates.merge_plates_compute`` builds a merged
``pipeline_info.json`` as ``dict(info1)`` — plate 1's sidecar, wholesale. Any
analysis block that cannot be combined by rule is therefore **kept from plate 1
and dropped from plate 2**, and ``_dropped_blocks_warning`` is what tells the
user that happened. It iterates ``_UNMERGEABLE_BLOCKS``, a roster written by
hand, and for most of its life nothing held that roster against anything:
``grep -rn "_UNMERGEABLE_BLOCKS" tests/`` returned nothing at all.

So a block nobody remembered to add was merged silently — plate 2's version
gone, plate 1's kept, no warning. ``run_report`` was missing for exactly that
reason, and it is the one block whose merge is *also* wrong in a second way:
the merge remaps well labels, so plate 1's per-well records would be keyed on
the labels they had before the remap.

This guard is stated against the thing the roster is a list of, rather than
against a second list: the pipeline declares each block's sidecar key as a
module-level ``*_BLOCK`` constant, and a new block is covered by having been
named. CLAUDE.md §3 — "where it truly cannot be derived, write the roster once
and guard it *against the thing it is a list of*; a guard that holds one roster
against another has moved the problem up a level."
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import REPO_ROOT, parse_source

from datasets.merge_plates.merge_plates_compute import _UNMERGEABLE_BLOCKS

#: Blocks a merge may legitimately carry across, with the reason. An exemption
#: is a row with a reason, never an absence — a block left off on purpose and a
#: block nobody remembered look identical in code six months later.
_MERGEABLE = {
    # None yet. Every block the pipeline declares so far is a per-plate
    # measurement or decision; the first genuinely combinable one goes here
    # with the argument for why written beside it.
}


def _declared_blocks() -> dict[str, str]:
    """``{sidecar key: module}`` for every ``*_BLOCK`` constant in ``pipeline/``."""
    found: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "pipeline").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id.endswith("_BLOCK")
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    found[node.value.value] = path.name
    return found


def test_the_scan_finds_the_blocks_it_is_supposed_to():
    """A guard whose scan comes back empty passes for ever and measures
    nothing — the failure mode that makes a blind guard report as a greener
    suite."""
    declared = _declared_blocks()
    assert declared, "no *_BLOCK constants found under pipeline/ — scan drifted"
    assert "field_quality" in declared
    assert "run_report" in declared


@pytest.mark.parametrize("key", sorted(_declared_blocks()))
def test_every_declared_block_is_rostered_or_exempt(key):
    rostered = {k for k, _label in _UNMERGEABLE_BLOCKS}
    assert key in rostered or key in _MERGEABLE, (
        f"pipeline declares the sidecar block {key!r} and merge_plates does not "
        f"know about it. A merged plate will silently keep plate 1's copy and "
        f"drop plate 2's, with no warning. Add it to _UNMERGEABLE_BLOCKS with "
        f"the words to describe it, or to this module's _MERGEABLE with the "
        f"reason it can be combined."
    )


def test_every_roster_entry_still_names_a_real_block():
    """The reverse direction. An entry for a block nobody writes any more is a
    warning that can never fire, reading as coverage."""
    declared = set(_declared_blocks())
    # Blocks written by the Review side rather than declared in ``pipeline/``.
    # Listed because they are real and this scan cannot see them, not because
    # they are exempt from the roster.
    review_side = {"cell_gating", "ratios", "qc_options", "smfish_thresholds"}
    stale = [k for k, _ in _UNMERGEABLE_BLOCKS
             if k not in declared and k not in review_side]
    assert not stale, f"roster entries naming no block anyone writes: {stale}"


def test_each_roster_entry_carries_words_to_describe_it():
    """The warning quotes the label. An entry with a blank one produces a
    sentence naming nothing."""
    for key, label in _UNMERGEABLE_BLOCKS:
        assert key and label and not label.startswith("_"), (key, label)
