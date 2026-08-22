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
labels, where each state sits alone inside its own pair — pairing a state with
the words shown for it is presentation, not a second definition of the set.
"""

from __future__ import annotations

import ast

from services.run_journal import WELL_STATES
from tests._repo_scan import python_sources, rel

#: Below this many of the six names, a collection is something else — a tuple
#: grouping two related states for one count, or one state beside unrelated
#: strings.
_ROSTER_MIN = 3


def test_no_module_restates_the_journal_well_states():
    offenders = []
    for path in python_sources():
        # The definition itself, and nothing else, is allowed to spell them out.
        if path.parts[-2:] == ("services", "run_journal.py"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            # Direct string elements only, so a roster nested one level down
            # inside label pairs is not counted — see the module docstring.
            names = {e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            hits = sorted(names & set(WELL_STATES))
            if len(hits) >= _ROSTER_MIN:
                offenders.append(f"{rel(path)}:{node.lineno} — {hits}")
    assert not offenders, (
        "A module writes out its own list of run-journal well states. Import "
        "`services.run_journal.WELL_STATES` instead, so a state the runner "
        "records cannot be one a reader has never heard of (ARCHITECTURE §16). "
        f"Current roster: {list(WELL_STATES)}\n  " + "\n  ".join(offenders)
    )
