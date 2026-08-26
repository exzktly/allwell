"""The dataset-health states have one home (ARCHITECTURE §16).

``pipeline.completeness`` holds the rule that classifies a well as complete,
partial, missing or no_output, and the roster of those four names. Two surfaces
paint them — the Datasets Validate dashboard and the Review Home page — and both
describe the same wells, so a well that reads green in one and amber in the other
is not a rendering difference, it is two disagreeing answers to the same
question. That is the shape the shared predicate exists to make impossible.

A duplicated *classifier* is hard to detect by AST and easy to spot in review. A
duplicated *roster* is the opposite: it looks like a harmless four-line constant,
it is what shipped here before the extraction, and it is the thing a state added
or renamed five months from now will silently leave behind. So that is what this
guards.

The threshold is three of four rather than all four on purpose. §7.5 records a
roster that had gone six-of-eight stale without anyone noticing; a copy that has
already dropped a state is more suspect than a complete one, not less, and a
guard demanding an exact match would wave it through.
"""

from __future__ import annotations

import ast

from pipeline.completeness import HEALTH_STATES
from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

#: Below this many of the four names, a collection is something else — the
#: `("complete", "Complete")` pairs a legend builds, or one state mentioned
#: beside unrelated strings.
_ROSTER_MIN = 3


def test_no_module_restates_the_dataset_health_roster():
    offenders = []
    for path in python_sources():
        # The definition itself, and nothing else, is allowed to spell them out.
        if path.parts[-2:] == ("pipeline", "completeness.py"):
            continue
        tree = parse_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            # Direct string elements only. A legend's tuple-of-pairs holds one
            # state per inner tuple and never trips this, which is correct —
            # pairing each state with its label is presentation, not a second
            # roster.
            names = {e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            hits = sorted(names & set(HEALTH_STATES))
            if len(hits) >= _ROSTER_MIN:
                offenders.append(f"{rel(path)}:{node.lineno} — {hits}")
    assert not offenders, (
        "A module writes out its own list of dataset-health states. Import "
        "`pipeline.completeness.HEALTH_STATES` instead, so the states a plate "
        "legend paints cannot diverge from the states the classifier can "
        f"return (ARCHITECTURE §16). Current roster: {list(HEALTH_STATES)}\n  "
        + "\n  ".join(offenders)
    )


def test_the_roster_is_what_the_classifier_can_actually_return():
    """The roster is only worth guarding if it is true.

    Walks every combination of ``health``'s five inputs and asserts the answers
    are exactly ``HEALTH_STATES`` — no state the classifier can return is
    missing from the roster, and no state in the roster is unreachable.
    """
    from pipeline.completeness import health

    seen = set()
    for missing in ((), (("F001", "24h"),)):
        for csv_state in ("ok", "empty", "unreadable", "missing"):
            for has_in in (True, False):
                for has_zip in (True, False):
                    for has_folder in (True, False):
                        seen.add(health(
                            missing=missing, csv_state=csv_state,
                            has_in_container=has_in, has_out_zip=has_zip,
                            has_out_folder=has_folder))
    assert seen == set(HEALTH_STATES)
