"""Which rule decided a nanowell well's roster, as its grid record states it (tracker X35a-R4).

Stdlib only, and in ``domain/`` because two panes read it: Datasets (Validate, Merge Plates) and Review's
Nanowells page. The pipeline writes the roster rule into each well's ``nanowells/<well>_grid.json`` under
``array_radius``; a well analysed before the rule existed has a radius and no roster. A dataset can hold wells of
both rules (a well forced to re-run, or added, after the others), and the two count the nanowells at a circular
array's rim differently, so a place that counts rows across wells says so.
"""

from __future__ import annotations

#: A circle's roster decided by every site within the shared radius plus a quarter pitch: it holds a ring of
#: floor beyond the rim, written ``occluded``.
DISK = "disk"
#: A circle's roster decided by the evidence (``pipeline.nanowell_run._evidence_roster``).
EVIDENCE = "evidence"
#: An inferred array's roster (``--nanowell_geometry infer``).
INFERRED = "inferred"


def roster_rule(array_radius) -> str | None:
    """The rule a grid record's ``array_radius`` block names, or ``None`` when the block says nothing."""
    if not isinstance(array_radius, dict):
        return None
    if array_radius.get("roster_rule") == EVIDENCE:
        return EVIDENCE
    if array_radius.get("roster") is not None and array_radius.get("radius_um") is None:
        return INFERRED
    return DISK if array_radius.get("radius_um") is not None else None


def mixed_rules_note(rules: dict) -> str | None:
    """A sentence naming the wells of each circle rule when *rules* (``well -> rule``) holds both, else ``None``."""
    disk = sorted(w for w, r in rules.items() if r == DISK)
    evidence = sorted(w for w, r in rules.items() if r == EVIDENCE)
    if not disk or not evidence:
        return None
    return (f"Wells {', '.join(disk)} keep an earlier version's nanowell roster (every site within the array's radius "
            f"plus a quarter pitch, a ring of floor beyond the rim counted as occluded) and wells "
            f"{', '.join(evidence)} the current one (the sites the images support), so their counts of nanowells at "
            "the rim are not comparable; re-run the older wells with Force to align them")
