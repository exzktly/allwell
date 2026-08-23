"""The run journal's well states have one home (ARCHITECTURE §16).

``services.run_journal.WELL_STATES`` names what a run did to a well —
``uploaded`` / ``analysed`` / ``fetched`` / ``skipped`` / ``failed`` /
``not-fetched``. Four surfaces read those names back: the remote fetch
reconciliation, the Analyze resume banner, the pre-run readout, and the
Datasets Validate scan. Each one turns a state into a sentence for the user, so
a state a writer records and a reader has never heard of does not fail — it
falls into whatever fallback that reader has, and the well is reported as
something it is not.

That failure is silent by construction, which is why it is worth a guard rather
than review. It is also exactly the failure ``test_no_second_completeness_predicate``
exists to prevent for the *health* states, and this is its twin: a duplicated
roster looks like a harmless constant, and it is what a state added or renamed
later will quietly leave behind.

The threshold is three names rather than all six, for the same reason that test
gives: a copy that has already fallen behind the real roster is more suspect
than a complete one, not less, and a guard demanding an exact match waves the
stale copy through.

Note what is deliberately *not* forbidden. A single state named in a comparison
(``state == "fetched"``) is ordinary code. So is a mapping from states to
labels — pairing a state with the words shown for it is presentation, not a
second definition of the set — **provided the module also reaches for
``WELL_STATES``**. That proviso is the lesson of the defect that got past the
first version of this guard.

The first version walked tuples, lists and sets only, on the reasoning above
that a label mapping is presentation. It is, right up until something iterates
it. ``_reconcile_fetch``'s summary was built by looping over its phrase table,
so a state recorded by a writer but not spelled there appeared in the per-well
lines and in no bucket beneath them — the exact "the parts stopped summing to
the whole" bug this branch had just fixed, re-created one commit later in a
shape the guard could not see.

Hence the rule now: a dict whose keys are the states is fine as *words*, but a
module that spells the states out and never consults the canonical roster has
no way to order or complete anything from it, and is one state away from
dropping one silently.
"""

from __future__ import annotations

import ast

from services.run_journal import WELL_STATES
from tests._repo_scan import python_sources, rel

#: Below this many of the six names, a collection is something else — a tuple
#: grouping two related states for one count, or one state beside unrelated
#: strings.
_ROSTER_MIN = 3


def _string_elements(node) -> set[str]:
    """The plain string members of a collection, or the keys of a dict.

    Direct members only, so a roster nested one level down inside label pairs
    is not counted — see the module docstring.
    """
    elts = node.keys if isinstance(node, ast.Dict) else node.elts
    return {e.value for e in elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def test_no_module_restates_the_journal_well_states():
    offenders = []
    for path in python_sources():
        # The definition itself, and nothing else, is allowed to spell them out.
        if path.parts[-2:] == ("services", "run_journal.py"):
            continue
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        # A module that reaches for the canonical roster may also carry a table
        # of words for the states; one that never does is holding the only copy
        # it has, which is what makes a missing state unrecoverable there.
        consults_roster = "WELL_STATES" in source
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                continue
            if isinstance(node, ast.Dict) and consults_roster:
                continue
            hits = sorted(_string_elements(node) & set(WELL_STATES))
            if len(hits) >= _ROSTER_MIN:
                offenders.append(f"{rel(path)}:{node.lineno} — {hits}")
    assert not offenders, (
        "A module writes out its own list of run-journal well states. Import "
        "`services.run_journal.WELL_STATES` instead, so a state the runner "
        "records cannot be one a reader has never heard of (ARCHITECTURE §16). "
        f"Current roster: {list(WELL_STATES)}\n  " + "\n  ".join(offenders)
    )
