"""(De)serialization of replicate sets + bar groups to/from plain dicts.

Pure data transforms over the :mod:`well_viewer.domain.batch_models` types, with
no UI / feature dependencies. Lives in ``domain`` so the domain layer
(``sample_definitions``) can persist bar-group state without importing up into
the barplot *feature*. The barplot feature re-exports these names for
backward compatibility (``well_viewer.features.barplot.controller``).
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from well_viewer.domain.batch_models import BarGroup, ReplicateSet


def bar_groups_to_dict(
    rep_sets: List[ReplicateSet],
    bar_groups: List[BarGroup],
    *,
    extract_well_token,
) -> dict:
    rep_list = [{"name": r.name, "wells": list(r.wells)} for r in rep_sets]
    grp_list = []
    for grp in bar_groups:
        grp_list.append(
            {
                "name": grp.name,
                "hidden": grp.hidden,
                "members": [r.name for r in grp.members],
                "solo_wells": list(grp.solo_wells),
            }
        )
    return {"rep_sets": rep_list, "groups": grp_list}


def bar_groups_from_data(data, *, tok_to_label: dict[str, str]) -> Tuple[List[ReplicateSet], List[BarGroup]]:
    def _norm(tok: str) -> str:
        tok = tok.strip().upper()
        m = re.match(r"^([A-H])(\d{1,2})$", tok, re.I)
        return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else tok

    def _valid_tok(raw: str) -> Optional[str]:
        n = _norm(raw)
        return n if n in tok_to_label else None

    rep_sets: List[ReplicateSet] = []
    bar_groups: List[BarGroup] = []

    if not isinstance(data, dict):
        return rep_sets, bar_groups
    for item in data.get("rep_sets", []):
        wells = [t for t in (_valid_tok(x) for x in item.get("wells", [])) if t is not None]
        rep_sets.append(ReplicateSet(item.get("name", "R"), wells))
    rep_by_name = {r.name: r for r in rep_sets}
    for item in data.get("groups", []):
        grp = BarGroup(item.get("name", "Group"), hidden=bool(item.get("hidden", False)))
        for rname in item.get("members", []):
            if rname in rep_by_name:
                grp.members.append(rep_by_name[rname])
        for raw_tok in item.get("solo_wells", []):
            n = _valid_tok(raw_tok)
            if n:
                grp.solo_wells.append(n)
        bar_groups.append(grp)

    return rep_sets, bar_groups
